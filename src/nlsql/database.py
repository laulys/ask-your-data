"""Database access and schema introspection.

SQLAlchemy gives the same API over SQLite and MySQL, so the agent code does not
care which one is behind the URL. What does differ is the SQL *dialect* the
model should write in, so that is exposed explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

__all__ = ["Database", "QueryResult"]

# SQLAlchemy dialect name -> sqlglot dialect name
_DIALECT_MAP = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "mariadb": "mysql",
    "postgresql": "postgres",
    "duckdb": "duckdb",
}


@dataclass
class QueryResult:
    """Rows returned by a query, plus the SQL that produced them."""

    sql: str
    columns: list[str]
    rows: list[tuple[Any, ...]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=self.columns)

    def to_markdown(self, limit: int = 20) -> str:
        """A compact table, used to show the model what came back."""
        if not self.columns:
            return "(no columns)"
        if not self.rows:
            return "(no rows)"

        shown = self.rows[:limit]
        header = " | ".join(self.columns)
        divider = " | ".join("---" for _ in self.columns)
        body = "\n".join(
            " | ".join("NULL" if value is None else str(value) for value in row) for row in shown
        )
        table = f"{header}\n{divider}\n{body}"
        if self.row_count > limit:
            table += f"\n... ({self.row_count - limit} more rows)"
        return table


class Database:
    """A read-only view of a SQL database."""

    def __init__(self, url: str, *, engine: Engine | None = None) -> None:
        self.url = url
        self.engine = engine or create_engine(url, future=True)
        self._schema_cache: str | None = None

    @property
    def dialect(self) -> str:
        """The sqlglot dialect name for this connection."""
        return _DIALECT_MAP.get(self.engine.dialect.name, self.engine.dialect.name)

    def table_names(self) -> list[str]:
        return sorted(inspect(self.engine).get_table_names())

    def run(self, sql: str, *, max_rows: int | None = None) -> QueryResult:
        """Execute ``sql`` and collect the rows.

        The connection is never committed, so even if a write slipped past the
        safety guard it would be rolled back when the block exits.
        """
        with self.engine.connect() as connection:
            cursor = connection.execute(text(sql))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(max_rows) if max_rows else cursor.fetchall()
            return QueryResult(sql=sql, columns=columns, rows=[tuple(row) for row in rows])

    def schema_description(self, *, include_row_counts: bool = True, refresh: bool = False) -> str:
        """A compact, text description of the schema to put in the prompt.

        Reads as DDL rather than JSON because models were trained on far more
        ``CREATE TABLE`` than on schema JSON, and it is shorter.
        """
        if self._schema_cache is not None and not refresh:
            return self._schema_cache

        inspector = inspect(self.engine)
        blocks: list[str] = []

        for table in sorted(inspector.get_table_names()):
            foreign_keys = {}
            for fk in inspector.get_foreign_keys(table):
                target = fk.get("referred_table")
                referred = fk.get("referred_columns") or []
                for position, column in enumerate(fk.get("constrained_columns") or []):
                    target_column = referred[position] if position < len(referred) else ""
                    foreign_keys[column] = f"{target}.{target_column}".rstrip(".")

            try:
                primary_keys = set(
                    inspector.get_pk_constraint(table).get("constrained_columns") or []
                )
            except Exception:  # pragma: no cover - some backends lack PK metadata
                primary_keys = set()

            entries: list[tuple[str, str]] = []
            for column in inspector.get_columns(table):
                name = column["name"]
                notes = []
                if name in primary_keys:
                    notes.append("PRIMARY KEY")
                if name in foreign_keys:
                    notes.append(f"REFERENCES {foreign_keys[name]}")
                comment = f"  -- {', '.join(notes)}" if notes else ""
                entries.append((f"{name} {column['type']}", comment))

            lines = [f"TABLE {table} ("]
            for index, (declaration, comment) in enumerate(entries):
                separator = "," if index < len(entries) - 1 else ""
                lines.append(f"  {declaration}{separator}{comment}")
            lines.append(")")

            if include_row_counts:
                count = self._row_count(table)
                if count is not None:
                    lines.append(f"-- {count:,} rows")

            blocks.append("\n".join(lines))

        self._schema_cache = "\n\n".join(blocks) if blocks else "(the database has no tables)"
        return self._schema_cache

    def _row_count(self, table: str) -> int | None:
        quoted = self.engine.dialect.identifier_preparer.quote(table)
        try:
            with self.engine.connect() as connection:
                return int(connection.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar_one())
        except Exception:  # pragma: no cover - a view or permission issue
            return None
