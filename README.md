# 🗄️ Natural Language SQL Agent

Ask a SQL database questions in plain English. The agent reads the schema, writes the
query, **proves the query only reads data**, runs it, and explains what came back.

![The agent answering a question end to end](docs/demo.gif)

*Real time, on a laptop, with an 8B model running locally — no API key.*

```console
$ nlsql "Which country's customers spent the most?"

SELECT c.Country AS Country, SUM(i.Total) AS TotalSpent
FROM Customer AS c
JOIN Invoice AS i ON c.CustomerId = i.CustomerId
GROUP BY c.Country
ORDER BY TotalSpent DESC
LIMIT 1

Country  TotalSpent
USA      523.06

╭─────────────────────────── answer ───────────────────────────╮
│ The USA customers spent the most by invoice, totaling        │
│ $523.06.                                                     │
╰──────────────────────────────────────────────────────────────╯
3.9s · 1 row
```

---

## Why this exists

This started as an IBM Skills Network guided project, *Build a Natural Language SQL
Agent*. That lab runs inside a hosted environment with a hard-coded MySQL host, free
watsonx credentials that only work from inside IBM's own network, and a LangChain
ReAct agent that stops at printing to the terminal.

I rebuilt it as something that **runs on anyone's laptop**, and spent the effort on
the parts the lab skips: what happens when the model writes a query that deletes
data, and what happens when the query is simply wrong.

| | The lab | This project |
|---|---|---|
| Credentials | `project_id="skills-network"`, no API key, IBM-only | Four interchangeable backends; works with no API key at all |
| Database | MySQL at a hard-coded lab IP | SQLite out of the box, MySQL via Docker |
| If the model writes `DROP TABLE` | Executed | Parsed, refused, never reaches the database |
| If the query errors | Crashes, or loops on a parsing error | Error is fed back; the agent rewrites and retries |
| If the result is huge | Returns everything | `LIMIT` injected and clamped |
| Interface | `print()` | Web app + CLI, both showing the generated SQL |
| Tests | None | 81, covering the guard, the repair loop and the CLI, on CI across Python 3.10–3.12 |

---

## Quickstart

No API key needed — the default setup runs a model on your own machine.

```bash
git clone https://github.com/laulys/nl-sql-agent.git && cd nl-sql-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"
python scripts/setup_data.py          # downloads the Chinook sample database
cp .env.example .env
```

Install [Ollama](https://ollama.com) and pull a model (about 5 GB):

```bash
ollama pull qwen3:8b
```

Then ask it something:

```bash
nlsql "How many albums are there in the database?"
```

Or open the web app:

```bash
streamlit run app/streamlit_app.py
```

### Using a hosted model instead

Set two lines in `.env` and nothing else changes:

```bash
NLSQL_PROVIDER=openai       # or anthropic, or watsonx
OPENAI_API_KEY=sk-...
```

```bash
pip install -e ".[openai]"   # or .[anthropic] / .[watsonx]
```

---

## How it works

```mermaid
flowchart LR
    Q["Question<br/>in English"] --> P["Prompt<br/>+ live schema"]
    P --> M(["LLM"])
    M -->|SQL| G{"Safety guard<br/>parse the AST"}
    G -->|"write or<br/>multi-statement"| X["Refused<br/>never executed"]
    G -->|"single SELECT<br/>+ LIMIT"| DB[("Database")]
    DB -->|error| R["Repair:<br/>error back to the model"]
    R --> M
    DB -->|rows| A(["LLM explains<br/>the rows"])
    A --> OUT["Answer + SQL<br/>+ table + chart"]
```

Five small pieces, each testable on its own:

| Module | Job |
|---|---|
| [`database.py`](src/nlsql/database.py) | Connects, and turns the live schema into compact DDL for the prompt |
| [`safety.py`](src/nlsql/safety.py) | Parses generated SQL and refuses anything that is not one read-only statement |
| [`llm.py`](src/nlsql/llm.py) | One interface, four backends |
| [`agent.py`](src/nlsql/agent.py) | The loop: generate → guard → run → repair → explain |
| [`prompts.py`](src/nlsql/prompts.py) | Every prompt, in one readable file |

### The safety guard

A text-to-SQL tool hands a language model a database connection. Asking the model
nicely not to delete anything is not a control. So generated SQL is parsed into a
syntax tree with [`sqlglot`](https://github.com/tobymao/sqlglot) and rejected unless
the tree is a single read-only statement:

```python
>>> enforce_read_only("SELECT * FROM Album", "sqlite", max_rows=200)
'SELECT * FROM Album LIMIT 200'              # limit injected

>>> enforce_read_only("SELECT 1; DROP TABLE Album", "sqlite")
UnsafeSQLError: Expected a single statement, got 2.

>>> enforce_read_only("PRAGMA table_info(Album)", "sqlite")
UnsafeSQLError: Only read-only queries are allowed, got PRAGMA.
```

It blocks writes, DDL, chained statements, `SELECT ... INTO`, and the commands
`PRAGMA`/`ATTACH` that reach outside the current query — while still allowing CTEs,
`UNION`, subqueries and joins. An over-large `LIMIT` is clamped rather than trusted.

This is one layer, not the only one. The connection is never committed, and in
production you would also connect as a user holding nothing but `SELECT`.

### Self-repair

The model's first query is often wrong — a column that does not exist, a join in the
wrong direction. Rather than failing, the agent hands the database's own error message
back to the model and asks for a correction, up to `NLSQL_MAX_REPAIRS` times. The web
app shows every failed attempt in an expander, because hiding them would make the demo
look smoother and teach you less.

### Questions that need no query

"Describe the PlaylistTrack table" is a question about the schema, not the data. The
model answers it directly from the schema already in its prompt instead of inventing a
query to run. The same path handles "delete everything", which gets a flat refusal.

---

## Results

![Chart view](docs/app-chart.jpg)

Measured on an M-series Mac with `qwen3:8b` running locally — no API, no network:

| Question | Time | Answer |
|---|---|---|
| How many albums are there? | 7.7s | 347 |
| Which country's customers spent the most? | 3.9s | USA, $523.06 |
| Top 5 best selling artists by revenue? | 10.6s | Iron Maiden, $138.60 |
| Describe the PlaylistTrack table | 4.3s | *answered from the schema, no query* |
| Delete every row in the Album table | 3.5s | *refused, read-only* |

All figures match a hand-written query against Chinook.

---

## Testing

```bash
pytest                       # 80 passed, 1 skipped (that one needs the OpenAI SDK)
pytest --cov=nlsql           # 87% overall; agent.py 100%
ruff check . && ruff format --check .
```

The suite runs against a small purpose-built SQLite database and a scripted fake
model, so it needs no network, no API key and no downloads. What it checks:

- every write, DDL and injection shape the guard must block
- `LIMIT` injection and clamping
- the repair loop, including that an unsafe query is refused *and* retried
- SQL extraction from fenced blocks, bare text and `<think>` reasoning blocks
- dialect detection for MySQL and Postgres URLs, with no driver installed
- the CLI's exit codes

CI runs the same suite on Python 3.10, 3.11 and 3.12.

---

## Running against MySQL

To use MySQL, the way the original lab does:

```bash
python scripts/setup_data.py --mysql
docker compose up -d
```

```bash
NLSQL_DATABASE_URL=mysql+pymysql://root:chinook@127.0.0.1:3306/Chinook
```

The agent detects the dialect from the URL and writes MySQL rather than SQLite.

> Heads up: the SQLite path is the one I have run end to end. The MySQL path is
> covered by unit tests for URL and dialect handling, but I have not run the
> container myself — Docker is not installed on the machine I built this on.

---

## Configuration

Every setting has a working default; see [`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `NLSQL_PROVIDER` | `ollama` | `ollama`, `openai`, `anthropic` or `watsonx` |
| `NLSQL_DATABASE_URL` | `sqlite:///data/chinook.db` | Any SQLAlchemy URL |
| `NLSQL_MAX_ROWS` | `200` | Row ceiling applied to every generated query |
| `NLSQL_MAX_REPAIRS` | `2` | How many times the agent may rewrite a failing query |

---

## The data

[Chinook](https://github.com/lerocha/chinook-database) — a digital music store:
11 tables, ~15,000 rows, real foreign keys. Track metadata comes from a real iTunes
library; customers and invoices are generated. It is a good text-to-SQL benchmark
because most interesting questions need a three- or four-table join.

---

## What I would do next

- **Evaluation harness.** Right now "it works" means I checked the answers by hand.
  A set of question/expected-SQL pairs scored automatically would catch regressions
  when a prompt changes.
- **Schema retrieval.** The whole schema goes in the prompt. That is fine for 11
  tables and breaks somewhere past a few hundred; the fix is retrieving only the
  relevant tables.
- **Query cost limits.** A `LIMIT` caps rows returned, not work done. A cartesian
  join over large tables would still hurt, so a statement timeout belongs here.

---

## Credits

Built on the IBM Skills Network guided project *Build a Natural Language SQL Agent*
by Ricky Shi. The idea and the choice of dataset are from the course; the
architecture, the safety layer, the repair loop, the provider abstraction, the
interfaces and the tests are my own work.

MIT licensed. Chinook is MIT licensed, by Luis Rocha.
