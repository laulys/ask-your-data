"""Command line interface.

nlsql "Which country's customers spent the most?"
nlsql --interactive
nlsql --schema
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from nlsql.agent import AgentResult, SQLAgent
from nlsql.config import Settings
from nlsql.database import Database
from nlsql.llm import build_llm

console = Console()

EXAMPLES = [
    "How many albums are there in the database?",
    "Which country's customers spent the most?",
    "Who are the top 5 best selling artists by revenue?",
    "What is the average invoice total per country?",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nlsql",
        description="Ask a SQL database questions in plain English.",
        epilog='Example: nlsql "Which country\'s customers spent the most?"',
    )
    parser.add_argument("question", nargs="*", help="the question to answer")
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="keep asking questions in a loop"
    )
    parser.add_argument("--schema", action="store_true", help="print the database schema and exit")
    parser.add_argument("--sql-only", action="store_true", help="print the SQL, skip the summary")
    parser.add_argument("--provider", help="override NLSQL_PROVIDER (ollama, openai, ...)")
    parser.add_argument("--model", help="override the model name for the provider")
    parser.add_argument("--database-url", help="override NLSQL_DATABASE_URL")
    parser.add_argument("--max-rows", type=int, help="maximum rows a generated query may return")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = Settings.from_env()
    except ValueError as err:
        console.print(f"[red]Configuration error:[/red] {err}")
        return 2

    provider = args.provider or settings.provider
    database_url = args.database_url or settings.database_url
    max_rows = args.max_rows or settings.max_rows

    try:
        database = Database(database_url)
    except Exception as err:
        console.print(f"[red]Could not open the database:[/red] {err}")
        return 1

    if args.schema:
        console.print(Syntax(database.schema_description(), "sql", theme="ansi_dark"))
        return 0

    try:
        if not database.table_names():
            console.print(
                "[red]The database has no tables.[/red] "
                "Run [bold]python scripts/setup_data.py[/bold] to download the Chinook sample."
            )
            return 1
    except Exception as err:
        console.print(f"[red]Could not read the database:[/red] {err}")
        console.print("Check NLSQL_DATABASE_URL in your .env file.")
        return 1

    try:
        llm = build_llm(provider, args.model or settings.model)
    except (ValueError, RuntimeError) as err:
        console.print(f"[red]Could not set up the {provider} backend:[/red] {err}")
        return 2

    agent = SQLAgent(database, llm, max_rows=max_rows, max_repairs=settings.max_repairs)
    console.print(
        f"[dim]{provider}:{llm.model}  ·  {database.dialect}  ·  "
        f"{len(database.table_names())} tables[/dim]\n"
    )

    if args.interactive or not args.question:
        return _interactive(agent, sql_only=args.sql_only)

    return _answer_once(agent, " ".join(args.question), sql_only=args.sql_only)


def _answer_once(agent: SQLAgent, question: str, *, sql_only: bool) -> int:
    with console.status("[dim]thinking...[/dim]", spinner="dots"):
        result = agent.ask(question)
    _render(result, sql_only=sql_only)
    return 0 if result.succeeded else 1


def _interactive(agent: SQLAgent, *, sql_only: bool) -> int:
    console.print("Ask a question, or [bold]exit[/bold] to quit. Try one of these:")
    for example in EXAMPLES:
        console.print(f"  [dim]·[/dim] {example}")
    console.print()

    while True:
        try:
            question = console.input("[bold cyan]?[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return 0

        if question.lower() in {"exit", "quit", "q"}:
            console.print("[dim]bye[/dim]")
            return 0
        if not question:
            continue

        with console.status("[dim]thinking...[/dim]", spinner="dots"):
            result = agent.ask(question)
        _render(result, sql_only=sql_only)


def _render(result: AgentResult, *, sql_only: bool) -> None:
    for index, attempt in enumerate(result.attempts):
        if attempt.succeeded:
            continue
        console.print(
            Panel(
                f"[yellow]{attempt.error}[/yellow]",
                title=f"attempt {index + 1} failed, rewriting",
                border_style="yellow",
            )
        )

    if result.sql:
        console.print(Syntax(result.sql, "sql", theme="ansi_dark", word_wrap=True))
        console.print()

    if result.error:
        console.print(Panel(result.error, title="failed", border_style="red"))
        return

    if result.result is not None and result.result.rows and not sql_only:
        console.print(_table(result.result))
        console.print()

    if not sql_only and result.answer:
        console.print(Panel(result.answer, border_style="green", title="answer"))

    footer = f"{result.elapsed_seconds:.1f}s"
    if result.repair_count:
        footer += f"  ·  {result.repair_count} repair(s)"
    if result.result is not None:
        footer += f"  ·  {result.result.row_count} row(s)"
    console.print(f"[dim]{footer}[/dim]\n")


def _table(result, limit: int = 20) -> Table:
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in result.columns:
        table.add_column(str(column))
    for row in result.rows[:limit]:
        table.add_row(*["" if value is None else str(value) for value in row])
    if result.row_count > limit:
        table.caption = f"showing {limit} of {result.row_count} rows"
    return table


if __name__ == "__main__":
    sys.exit(main())
