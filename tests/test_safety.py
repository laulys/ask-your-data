"""The safety guard is the part that must not have holes, so it gets the most tests."""

from __future__ import annotations

import pytest

from nlsql.safety import UnsafeSQLError, enforce_read_only, extract_sql, strip_reasoning

READ_ONLY = [
    "SELECT * FROM album",
    "SELECT name FROM band WHERE band_id = 1",
    "WITH top AS (SELECT * FROM album) SELECT * FROM top",
    "SELECT COUNT(*) FROM album GROUP BY band_id HAVING COUNT(*) > 1",
    "SELECT a.title, b.name FROM album a JOIN band b ON a.band_id = b.band_id",
    "SELECT * FROM album UNION SELECT * FROM album",
    "SELECT (SELECT MAX(sales) FROM album) AS best",
]

WRITES = [
    "DELETE FROM album",
    "DROP TABLE album",
    "UPDATE album SET sales = 0",
    "INSERT INTO band (name) VALUES ('x')",
    "CREATE TABLE evil (id INT)",
    "ALTER TABLE album ADD COLUMN x INT",
    "TRUNCATE TABLE album",
    "PRAGMA table_info(album)",
    "ATTACH DATABASE 'other.db' AS other",
    "SELECT * INTO backup FROM album",
    "INSERT INTO backup SELECT * FROM album",
    "GRANT SELECT ON album TO someone",
]


@pytest.mark.parametrize("sql", READ_ONLY)
def test_read_only_queries_pass(sql: str) -> None:
    assert enforce_read_only(sql, "sqlite", max_rows=100)


@pytest.mark.parametrize("sql", WRITES)
def test_writes_are_blocked(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        enforce_read_only(sql, "sqlite", max_rows=100)


def test_chained_statements_are_blocked() -> None:
    """The classic injection: a harmless query with a second one stapled on."""
    with pytest.raises(UnsafeSQLError, match="single statement"):
        enforce_read_only("SELECT 1; DROP TABLE album", "sqlite")


def test_comment_hidden_statement_is_blocked() -> None:
    with pytest.raises(UnsafeSQLError):
        enforce_read_only("SELECT 1 -- harmless\n; DELETE FROM album", "sqlite")


def test_limit_is_added_when_missing() -> None:
    assert "LIMIT 25" in enforce_read_only("SELECT * FROM album", "sqlite", max_rows=25)


def test_limit_is_tightened_when_too_large() -> None:
    result = enforce_read_only("SELECT * FROM album LIMIT 10000", "sqlite", max_rows=50)
    assert "LIMIT 50" in result
    assert "10000" not in result


def test_smaller_limit_is_respected() -> None:
    result = enforce_read_only("SELECT * FROM album LIMIT 3", "sqlite", max_rows=500)
    assert "LIMIT 3" in result


def test_unparseable_sql_is_rejected() -> None:
    with pytest.raises(UnsafeSQLError):
        enforce_read_only("this is not sql at all ((", "sqlite")


def test_empty_sql_is_rejected() -> None:
    with pytest.raises(UnsafeSQLError):
        enforce_read_only("", "sqlite")


def test_max_rows_must_be_positive() -> None:
    with pytest.raises(ValueError):
        enforce_read_only("SELECT 1", "sqlite", max_rows=0)


def test_dialect_round_trip() -> None:
    """The same query normalises for whichever engine is behind the URL."""
    assert enforce_read_only("SELECT * FROM album", "mysql", max_rows=10)


class TestExtractSQL:
    def test_fenced_block(self) -> None:
        reply = "Here you go:\n```sql\nSELECT 1\n```\nHope that helps!"
        assert extract_sql(reply) == "SELECT 1"

    def test_fence_without_language(self) -> None:
        assert extract_sql("```\nSELECT 1\n```") == "SELECT 1"

    def test_bare_sql(self) -> None:
        assert extract_sql("SELECT name FROM band") == "SELECT name FROM band"

    def test_prose_before_sql_is_dropped(self) -> None:
        assert extract_sql("Sure thing. SELECT 1") == "SELECT 1"

    def test_trailing_semicolon_removed(self) -> None:
        assert extract_sql("SELECT 1;") == "SELECT 1"

    def test_reasoning_block_is_stripped(self) -> None:
        reply = "<think>Let me look at the schema...</think>\n```sql\nSELECT 1\n```"
        assert extract_sql(reply) == "SELECT 1"

    def test_unterminated_reasoning_block(self) -> None:
        """A model that runs out of tokens mid-thought still leaves usable SQL."""
        assert extract_sql("<think>hmm\nSELECT 1") == "SELECT 1"

    def test_cte_is_recognised(self) -> None:
        assert extract_sql("WITH t AS (SELECT 1) SELECT * FROM t").startswith("WITH")

    def test_empty_reply_raises(self) -> None:
        with pytest.raises(UnsafeSQLError):
            extract_sql("   ")


def test_strip_reasoning_leaves_plain_text() -> None:
    assert strip_reasoning("just an answer") == "just an answer"
