"""The command line entry point, including its exit codes.

Exit codes matter: this is the piece someone would wire into a shell script,
so 0 / 1 / 2 need to mean something consistent.
"""

from __future__ import annotations

import sqlite3

import pytest

from nlsql import cli


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own .env out of the tests."""
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: cls()))


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch, fake_llm_factory):
    def install(replies: list[str]):
        llm = fake_llm_factory(replies)
        monkeypatch.setattr(cli, "build_llm", lambda provider, model=None: llm)
        return llm

    return install


def test_schema_flag_prints_the_schema(sqlite_url, capsys) -> None:
    assert cli.main(["--schema", "--database-url", sqlite_url]) == 0
    assert "TABLE band" in capsys.readouterr().out


def test_answers_a_question(sqlite_url, fake_backend, capsys) -> None:
    fake_backend(["```sql\nSELECT name FROM band ORDER BY name\n```", "Blur and Pulp."])

    exit_code = cli.main(["--database-url", sqlite_url, "Which", "bands", "are", "there?"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Blur and Pulp." in output
    assert "SELECT" in output


def test_sql_only_skips_the_summary(sqlite_url, fake_backend, capsys) -> None:
    fake_backend(["```sql\nSELECT name FROM band\n```"])

    exit_code = cli.main(["--sql-only", "--database-url", sqlite_url, "bands?"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SELECT" in output
    # Only one model call was scripted, so the summary step never ran.


def test_failure_exits_nonzero(sqlite_url, fake_backend, capsys) -> None:
    fake_backend(["```sql\nSELECT * FROM missing\n```"] * 3)

    assert cli.main(["--database-url", sqlite_url, "anything?"]) == 1
    assert "failed" in capsys.readouterr().out.lower()


def test_unknown_provider_exits_two(sqlite_url, capsys) -> None:
    assert cli.main(["--provider", "crystal-ball", "--database-url", sqlite_url, "hi?"]) == 2
    assert "crystal-ball" in capsys.readouterr().out


def test_empty_database_points_at_the_setup_script(tmp_path, capsys) -> None:
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()

    assert cli.main(["--database-url", f"sqlite:///{path}", "anything?"]) == 1
    assert "setup_data.py" in capsys.readouterr().out


def test_unreachable_database_is_reported(capsys) -> None:
    assert cli.main(["--database-url", "sqlite:////nope/nowhere.db", "anything?"]) == 1
    assert "Could not" in capsys.readouterr().out


def test_max_rows_override_reaches_the_query(sqlite_url, fake_backend, capsys) -> None:
    fake_backend(["```sql\nSELECT * FROM album\n```", "Two albums."])

    cli.main(["--max-rows", "2", "--database-url", sqlite_url, "albums?"])

    assert "LIMIT 2" in capsys.readouterr().out
