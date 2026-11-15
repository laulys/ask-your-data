"""Agent behaviour, driven by a scripted model so the tests are deterministic."""

from __future__ import annotations

from nlsql.agent import SQLAgent
from nlsql.database import Database


def sql_reply(sql: str) -> str:
    return f"```sql\n{sql}\n```"


def test_happy_path(database: Database, fake_llm_factory) -> None:
    llm = fake_llm_factory([sql_reply("SELECT name FROM band ORDER BY name"), "Blur and Pulp."])
    result = SQLAgent(database, llm).ask("Which bands are there?")

    assert result.succeeded
    assert result.answer == "Blur and Pulp."
    assert result.result is not None
    assert result.result.rows == [("Blur",), ("Pulp",)]
    assert result.repair_count == 0
    assert result.elapsed_seconds >= 0


def test_schema_is_in_the_prompt(database: Database, fake_llm_factory) -> None:
    llm = fake_llm_factory([sql_reply("SELECT 1"), "One."])
    SQLAgent(database, llm).ask("anything")

    system_prompt = llm.calls[0][0]
    assert "TABLE band" in system_prompt
    assert "REFERENCES band.band_id" in system_prompt
    assert "sqlite" in system_prompt


def test_repairs_a_query_the_database_rejects(database: Database, fake_llm_factory) -> None:
    llm = fake_llm_factory(
        [
            sql_reply("SELECT name FROM musicians"),  # no such table
            sql_reply("SELECT name FROM band"),
            "Blur and Pulp.",
        ]
    )
    result = SQLAgent(database, llm).ask("Which bands are there?")

    assert result.succeeded
    assert result.repair_count == 1
    assert len(result.attempts) == 2
    assert not result.attempts[0].succeeded
    assert "musicians" in (result.attempts[0].error or "")

    repair_prompt = llm.calls[1][1]
    assert "failed" in repair_prompt.lower()
    assert "musicians" in repair_prompt


def test_repairs_a_query_the_guard_rejects(database: Database, fake_llm_factory) -> None:
    """An unsafe query is never executed, but the agent still gets to try again."""
    llm = fake_llm_factory(
        [sql_reply("DROP TABLE band"), sql_reply("SELECT name FROM band"), "Blur and Pulp."]
    )
    result = SQLAgent(database, llm).ask("Which bands are there?")

    assert result.succeeded
    assert result.repair_count == 1
    assert "read-only" in (result.attempts[0].error or "").lower()
    # The table is still there.
    assert database.run("SELECT COUNT(*) FROM band").rows == [(2,)]


def test_gives_up_after_max_repairs(database: Database, fake_llm_factory) -> None:
    llm = fake_llm_factory([sql_reply("SELECT * FROM nope")] * 3)
    result = SQLAgent(database, llm, max_repairs=2).ask("Which bands are there?")

    assert not result.succeeded
    assert result.error is not None
    assert len(result.attempts) == 3
    assert llm.replies == []  # it used exactly its allowance


def test_schema_question_skips_sql(database: Database, fake_llm_factory) -> None:
    llm = fake_llm_factory(["NO_SQL", "The album table stores titles and sales."])
    result = SQLAgent(database, llm).ask("Describe the album table")

    assert result.succeeded
    assert result.sql is None
    assert result.result is None
    assert "album table" in result.answer


def test_row_limit_is_applied(database: Database, fake_llm_factory) -> None:
    llm = fake_llm_factory([sql_reply("SELECT * FROM album"), "Three albums."])
    result = SQLAgent(database, llm, max_rows=2).ask("Show me the albums")

    assert result.sql is not None and "LIMIT 2" in result.sql
    assert result.result is not None and result.result.row_count == 2


def test_empty_question_is_rejected_without_calling_the_model(
    database: Database, fake_llm_factory
) -> None:
    llm = fake_llm_factory([])
    result = SQLAgent(database, llm).ask("   ")

    assert not result.succeeded
    assert llm.calls == []


def test_model_failure_is_reported_not_raised(database: Database, fake_llm_factory) -> None:
    class BrokenLLM(fake_llm_factory):  # type: ignore[misc]
        def generate(self, system, prompt, *, max_tokens=1024, temperature=0.0):
            raise RuntimeError("connection refused")

    result = SQLAgent(database, BrokenLLM([])).ask("Which bands are there?")

    assert not result.succeeded
    assert "connection refused" in (result.error or "")


def test_summary_failure_still_returns_the_rows(database: Database, fake_llm_factory) -> None:
    """If the model dies on the explain step, the data is still worth showing."""

    class FlakyLLM(fake_llm_factory):  # type: ignore[misc]
        def generate(self, system, prompt, *, max_tokens=1024, temperature=0.0):
            if self.calls:
                raise RuntimeError("backend went away")
            return super().generate(system, prompt, max_tokens=max_tokens, temperature=temperature)

    result = SQLAgent(database, FlakyLLM([sql_reply("SELECT name FROM band")])).ask("bands?")

    assert result.result is not None
    assert result.result.row_count == 2
    assert "backend went away" in result.answer
