"""Shared fixtures.

The tests run against a tiny purpose-built SQLite database rather than the
Chinook download, so the suite is fast and works on a fresh clone with no
network access.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nlsql.database import Database
from nlsql.llm import LLM


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    path = tmp_path / "test.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE band (
                band_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE album (
                album_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                band_id INTEGER REFERENCES band(band_id),
                sales INTEGER NOT NULL
            );
            INSERT INTO band (band_id, name) VALUES (1, 'Pulp'), (2, 'Blur');
            INSERT INTO album (album_id, title, band_id, sales) VALUES
                (1, 'Different Class', 1, 300),
                (2, 'His n Hers', 1, 120),
                (3, 'Parklife', 2, 250);
            """
        )
    return f"sqlite:///{path}"


@pytest.fixture
def database(sqlite_url: str) -> Database:
    return Database(sqlite_url)


class FakeLLM(LLM):
    """Returns canned replies in order, and records what it was asked."""

    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.model = "fake-1"
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        self.calls.append((system, prompt))
        if not self.replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)


@pytest.fixture
def fake_llm_factory():
    return FakeLLM
