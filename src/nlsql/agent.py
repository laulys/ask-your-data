"""The natural-language-to-SQL agent.

One pass looks like this::

    question -> LLM writes SQL -> safety guard -> database -> LLM explains rows

If the database rejects the query, the error text is handed back to the model
and it gets another go, up to ``max_repairs`` times. That loop is what makes
the difference between a demo that works on three questions and one that keeps
working on the fourth.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError

from nlsql import prompts
from nlsql.database import Database, QueryResult
from nlsql.llm import LLM
from nlsql.safety import UnsafeSQLError, enforce_read_only, extract_sql, strip_reasoning

__all__ = ["Attempt", "AgentResult", "SQLAgent"]

_MAX_ERROR_CHARS = 500


@dataclass
class Attempt:
    """One try at writing a query, kept so the UI can show the agent's work."""

    sql: str | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class AgentResult:
    """Everything one question produced."""

    question: str
    answer: str
    sql: str | None = None
    result: QueryResult | None = None
    attempts: list[Attempt] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None

    @property
    def repair_count(self) -> int:
        """How many times the agent had to rewrite its query."""
        return max(0, len(self.attempts) - 1)


class SQLAgent:
    """Answers questions about a database in plain language."""

    def __init__(
        self,
        database: Database,
        llm: LLM,
        *,
        max_rows: int = 200,
        max_repairs: int = 2,
    ) -> None:
        self.database = database
        self.llm = llm
        self.max_rows = max_rows
        self.max_repairs = max_repairs

    def ask(self, question: str) -> AgentResult:
        """Answer ``question`` against the database."""
        question = (question or "").strip()
        started = time.perf_counter()

        if not question:
            return AgentResult(
                question=question,
                answer="",
                error="Ask a question first.",
                elapsed_seconds=0.0,
            )

        schema = self.database.schema_description()
        system = prompts.SQL_SYSTEM.format(
            dialect=self.database.dialect,
            schema=schema,
            sentinel=prompts.NO_SQL_SENTINEL,
        )

        attempts: list[Attempt] = []
        user_prompt = prompts.SQL_USER.format(question=question)
        last_sql: str | None = None
        last_error: str | None = None

        for _ in range(self.max_repairs + 1):
            try:
                reply = self.llm.generate(system, user_prompt, max_tokens=800)
            except Exception as err:
                return self._failure(
                    question, f"The model backend failed: {err}", attempts, started
                )

            if self._is_schema_question(reply):
                answer = self._answer_from_schema(question, schema)
                return AgentResult(
                    question=question,
                    answer=answer,
                    attempts=attempts,
                    elapsed_seconds=time.perf_counter() - started,
                )

            raw_sql: str | None = None
            try:
                raw_sql = extract_sql(reply)
                safe_sql = enforce_read_only(
                    raw_sql, dialect=self.database.dialect, max_rows=self.max_rows
                )
            except UnsafeSQLError as err:
                last_sql, last_error = raw_sql, str(err)
                attempts.append(Attempt(sql=last_sql, error=last_error))
                user_prompt = self._repair_prompt(question, last_sql, last_error)
                continue

            try:
                result = self.database.run(safe_sql, max_rows=self.max_rows)
            except SQLAlchemyError as err:
                last_sql, last_error = safe_sql, _trim(str(err))
                attempts.append(Attempt(sql=last_sql, error=last_error))
                user_prompt = self._repair_prompt(question, last_sql, last_error)
                continue

            attempts.append(Attempt(sql=safe_sql))
            answer = self._explain(question, result)
            return AgentResult(
                question=question,
                answer=answer,
                sql=safe_sql,
                result=result,
                attempts=attempts,
                elapsed_seconds=time.perf_counter() - started,
            )

        return self._failure(
            question,
            f"Gave up after {len(attempts)} attempts. Last error: {last_error}",
            attempts,
            started,
            sql=last_sql,
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _is_schema_question(reply: str) -> bool:
        return strip_reasoning(reply).upper().startswith(prompts.NO_SQL_SENTINEL)

    def _answer_from_schema(self, question: str, schema: str) -> str:
        try:
            return self.llm.generate(
                prompts.SCHEMA_ANSWER_SYSTEM.format(schema=schema),
                prompts.SQL_USER.format(question=question),
                max_tokens=600,
            ).strip()
        except Exception as err:  # pragma: no cover - backend failure mid-run
            return f"Could not answer from the schema: {err}"

    def _explain(self, question: str, result: QueryResult) -> str:
        user = prompts.ANSWER_USER.format(
            question=question,
            sql=result.sql,
            row_count=result.row_count,
            table=result.to_markdown(limit=20),
        )
        try:
            answer = self.llm.generate(prompts.ANSWER_SYSTEM, user, max_tokens=500)
        except Exception as err:  # pragma: no cover - backend failure mid-run
            return f"The query ran, but the summary step failed: {err}"

        return strip_reasoning(answer) or "(the model returned an empty summary)"

    @staticmethod
    def _repair_prompt(question: str, sql: str | None, error: str) -> str:
        return prompts.REPAIR_USER.format(
            question=question,
            sql=sql or "(no valid SQL was produced)",
            error=error,
        )

    @staticmethod
    def _failure(
        question: str,
        message: str,
        attempts: list[Attempt],
        started: float,
        *,
        sql: str | None = None,
    ) -> AgentResult:
        return AgentResult(
            question=question,
            answer="",
            sql=sql,
            attempts=attempts,
            elapsed_seconds=time.perf_counter() - started,
            error=message,
        )


def _trim(text: str, limit: int = _MAX_ERROR_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " ..."
