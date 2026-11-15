"""Schema introspection and query execution."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError

from nlsql.database import Database, QueryResult


def test_dialect_is_reported_for_sqlglot(database: Database) -> None:
    assert database.dialect == "sqlite"


def test_mysql_url_maps_to_mysql_dialect() -> None:
    database = Database("mysql+pymysql://user:pw@localhost:3306/Chinook")
    assert database.dialect == "mysql"


def test_table_names_are_sorted(database: Database) -> None:
    assert database.table_names() == ["album", "band"]


def test_schema_description_includes_keys_and_counts(database: Database) -> None:
    schema = database.schema_description()

    assert "TABLE album (" in schema
    assert "PRIMARY KEY" in schema
    assert "REFERENCES band.band_id" in schema
    assert "-- 3 rows" in schema
    assert "-- 2 rows" in schema


def test_schema_description_is_cached(database: Database) -> None:
    first = database.schema_description()
    assert database.schema_description() is first


def test_run_returns_columns_and_rows(database: Database) -> None:
    result = database.run("SELECT name FROM band ORDER BY name")

    assert result.columns == ["name"]
    assert result.rows == [("Blur",), ("Pulp",)]
    assert result.row_count == 2


def test_run_respects_max_rows(database: Database) -> None:
    assert database.run("SELECT * FROM album", max_rows=1).row_count == 1


def test_bad_sql_raises_sqlalchemy_error(database: Database) -> None:
    with pytest.raises(SQLAlchemyError):
        database.run("SELECT * FROM does_not_exist")


class TestQueryResult:
    def test_to_dataframe(self, database: Database) -> None:
        frame = database.run("SELECT name FROM band ORDER BY name").to_dataframe()
        assert list(frame.columns) == ["name"]
        assert frame.shape == (2, 1)

    def test_to_markdown_renders_a_table(self) -> None:
        result = QueryResult(sql="x", columns=["a", "b"], rows=[(1, None)])
        markdown = result.to_markdown()
        assert "a | b" in markdown
        assert "NULL" in markdown

    def test_to_markdown_truncates(self) -> None:
        result = QueryResult(sql="x", columns=["n"], rows=[(i,) for i in range(50)])
        assert "... (30 more rows)" in result.to_markdown(limit=20)

    def test_to_markdown_handles_no_rows(self) -> None:
        assert QueryResult(sql="x", columns=["a"]).to_markdown() == "(no rows)"
