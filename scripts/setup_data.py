#!/usr/bin/env python3
"""Download the Chinook sample database.

Chinook models a digital music store: artists, albums, tracks, customers,
invoices. Eleven tables, around 15,000 rows, with real foreign keys - enough
structure that a text-to-SQL agent has to do actual joins.

    python scripts/setup_data.py            # SQLite file, ready to query
    python scripts/setup_data.py --mysql    # also fetch the MySQL dump

Source: https://github.com/lerocha/chinook-database (MIT licensed)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import httpx

RELEASE = "v1.4.5"
BASE_URL = f"https://github.com/lerocha/chinook-database/releases/download/{RELEASE}"
SQLITE_URL = f"{BASE_URL}/Chinook_Sqlite.sqlite"
MYSQL_URL = f"{BASE_URL}/Chinook_MySql.sql"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SQLITE_PATH = DATA_DIR / "chinook.db"
MYSQL_PATH = DATA_DIR / "chinook-mysql.sql"


def download(url: str, destination: Path) -> None:
    print(f"Downloading {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")

    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        written = 0
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=65_536):
                handle.write(chunk)
                written += len(chunk)
                if total:
                    print(f"\r  {written * 100 // total}%", end="", flush=True)
    temporary.replace(destination)
    shown = (
        destination.relative_to(Path.cwd())
        if destination.is_relative_to(Path.cwd())
        else destination
    )
    print(f"\r  saved to {shown}")


def verify_sqlite(path: Path) -> None:
    """Confirm the file really is the Chinook database."""
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = {"Album", "Artist", "Customer", "Invoice", "Track"} - tables
        if missing:
            raise SystemExit(f"Downloaded file is missing expected tables: {sorted(missing)}")
        albums = connection.execute("SELECT COUNT(*) FROM Album").fetchone()[0]
        tracks = connection.execute("SELECT COUNT(*) FROM Track").fetchone()[0]

    print(f"  {len(tables)} tables, {albums} albums, {tracks} tracks")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysql", action="store_true", help="also download the MySQL dump")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args(argv)

    if SQLITE_PATH.exists() and not args.force:
        print(f"{SQLITE_PATH} already exists (use --force to re-download)")
    else:
        download(SQLITE_URL, SQLITE_PATH)

    verify_sqlite(SQLITE_PATH)

    if args.mysql:
        if MYSQL_PATH.exists() and not args.force:
            print(f"{MYSQL_PATH} already exists (use --force to re-download)")
        else:
            download(MYSQL_URL, MYSQL_PATH)

    print('\nReady. Try:  nlsql "How many albums are there?"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
