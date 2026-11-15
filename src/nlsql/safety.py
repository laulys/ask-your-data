"""Static guard for model-generated SQL.

The agent never runs a string straight from the language model. Every candidate
query is parsed into an abstract syntax tree first, and is rejected unless the
tree is a single read-only statement. A row limit is then injected so a careless
``SELECT *`` on a large table cannot flood the process.

This is defence in depth, not the only defence: connect with a database user
that only holds SELECT grants as well.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

__all__ = ["UnsafeSQLError", "enforce_read_only", "extract_sql", "strip_reasoning"]


class UnsafeSQLError(ValueError):
    """Raised when generated SQL is not a single read-only statement."""


# Node types that write, change or reach outside the current query. Looked up by
# name so the module keeps importing across sqlglot versions that add or rename
# expressions. ``Command`` is the important catch-all: sqlglot parks anything it
# does not model (PRAGMA, ATTACH, VACUUM, CALL, ...) under it.
_FORBIDDEN_NAMES = (
    "Alter",
    "Analyze",
    "AttachOption",
    "Attach",
    "Call",
    "Command",
    "Commit",
    "Copy",
    "Create",
    "Delete",
    "Detach",
    "Drop",
    "Export",
    "Grant",
    "Insert",
    "Into",
    "LoadData",
    "Merge",
    "Pragma",
    "Refresh",
    "Revoke",
    "Rollback",
    "Set",
    "TruncateTable",
    "Transaction",
    "Update",
    "Use",
    "Vacuum",
)

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    node
    for node in (getattr(exp, name, None) for name in _FORBIDDEN_NAMES)
    if isinstance(node, type) and issubclass(node, exp.Expression)
)

# Statement roots the agent is allowed to run.
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = tuple(
    node
    for node in (
        getattr(exp, name, None)
        for name in ("Select", "Union", "Except", "Intersect", "Subquery", "Describe")
    )
    if isinstance(node, type) and issubclass(node, exp.Expression)
)

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LEADING_KEYWORD = re.compile(r"\b(WITH|SELECT)\b", re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove the ``<think>`` block that reasoning models emit before answering.

    Qwen3, DeepSeek-R1 and similar models wrap their scratchpad in ``<think>``
    tags. An unterminated block means the model ran out of tokens mid-thought,
    so the text after the last tag is kept.
    """
    cleaned = _THINK_BLOCK.sub("", text or "").strip()
    if "<think>" in cleaned.lower():
        cleaned = re.split(r"</?think>", cleaned, flags=re.IGNORECASE)[-1].strip()
    return cleaned


def extract_sql(text: str) -> str:
    """Pull a SQL statement out of a model's reply.

    Handles the three shapes models actually produce: a fenced ``sql`` block,
    bare SQL, and reasoning models that prefix their answer with a ``<think>``
    block.
    """
    if not text or not text.strip():
        raise UnsafeSQLError("The model returned an empty response.")

    cleaned = strip_reasoning(text)
    fenced = _SQL_FENCE.search(cleaned)
    candidate = fenced.group(1) if fenced else cleaned

    # Drop any prose before the statement starts.
    match = _LEADING_KEYWORD.search(candidate)
    if match:
        candidate = candidate[match.start() :]

    candidate = candidate.strip().rstrip(";").strip()
    if not candidate:
        raise UnsafeSQLError("No SQL statement found in the model's response.")
    return candidate


def enforce_read_only(sql: str, dialect: str = "sqlite", max_rows: int = 200) -> str:
    """Validate ``sql`` and return it normalised, with a row limit applied.

    Raises:
        UnsafeSQLError: if the text is not exactly one read-only statement.
    """
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")

    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except Exception as err:  # sqlglot raises several parse error types
        raise UnsafeSQLError(f"Could not parse the SQL: {err}") from err

    if not statements:
        raise UnsafeSQLError("No SQL statement found.")
    if len(statements) > 1:
        raise UnsafeSQLError(
            f"Expected a single statement, got {len(statements)}. "
            "Chained statements are not allowed."
        )

    root = statements[0]
    if not isinstance(root, ALLOWED_ROOTS):
        raise UnsafeSQLError(
            f"Only read-only queries are allowed, got {type(root).__name__.upper()}."
        )

    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(
                f"{type(node).__name__.upper()} is not allowed: this agent may only read data."
            )

    return _apply_limit(root, max_rows).sql(dialect=dialect, pretty=True)


def _apply_limit(root: exp.Expression, max_rows: int) -> exp.Expression:
    """Add a LIMIT, or tighten one that is larger than ``max_rows``."""
    if not hasattr(root, "limit"):
        return root

    existing = root.args.get("limit")
    if existing is not None:
        current = _limit_value(existing)
        if current is not None and current <= max_rows:
            return root

    try:
        return root.limit(max_rows)
    except Exception:  # pragma: no cover - defensive, depends on sqlglot internals
        return root


def _limit_value(limit_node: exp.Expression) -> int | None:
    expression = limit_node.args.get("expression", limit_node)
    try:
        return int(expression.name)
    except (AttributeError, TypeError, ValueError):
        return None
