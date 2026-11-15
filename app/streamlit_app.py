"""Streamlit front end.

    streamlit run app/streamlit_app.py

Shows the answer, the SQL the model wrote, the rows that came back and - in an
expander - every query it got wrong on the way. Hiding the failed attempts
would make the demo look smoother and teach the viewer less.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Allow `streamlit run app/streamlit_app.py` from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nlsql.agent import AgentResult, SQLAgent  # noqa: E402
from nlsql.config import Settings  # noqa: E402
from nlsql.database import Database  # noqa: E402
from nlsql.llm import build_llm  # noqa: E402

EXAMPLES = [
    "How many albums are there in the database?",
    "Which country's customers spent the most?",
    "Who are the top 5 best selling artists by revenue?",
    "What is the average invoice total per country?",
    "Which genre has the most tracks?",
    "Describe the PlaylistTrack table",
]

st.set_page_config(page_title="Natural Language SQL Agent", page_icon="🗄️", layout="wide")


@st.cache_resource(show_spinner=False)
def get_agent(provider: str, model: str | None, database_url: str, max_rows: int, max_repairs: int):
    database = Database(database_url)
    llm = build_llm(provider, model)
    return SQLAgent(database, llm, max_rows=max_rows, max_repairs=max_repairs), database, llm


def numeric_bar_chart(frame: pd.DataFrame) -> alt.Chart | None:
    """Chart the result when its shape is obviously chartable.

    Two columns, one label and one number, not too many rows. Anything else is
    better read as a table, so this returns None rather than guessing.
    """
    if frame.shape[1] != 2 or not (1 < len(frame) <= 50):
        return None

    label, value = frame.columns[0], frame.columns[1]
    if not pd.api.types.is_numeric_dtype(frame[value]):
        return None
    if pd.api.types.is_numeric_dtype(frame[label]) and frame[label].nunique() == len(frame):
        return None  # looks like an id column, not a category

    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X(f"{value}:Q", title=str(value)),
            y=alt.Y(f"{label}:N", sort="-x", title=None),
            tooltip=list(frame.columns),
        )
        .properties(height=min(400, 28 * len(frame) + 40))
    )


def render(result: AgentResult) -> None:
    st.markdown(f"#### {result.question}")

    if result.error:
        st.error(result.error)
    elif result.answer:
        st.success(result.answer)

    meta = [f"{result.elapsed_seconds:.1f}s"]
    if result.result is not None:
        meta.append(f"{result.result.row_count} rows")
    if result.repair_count:
        meta.append(f"{result.repair_count} self-repair(s)")
    st.caption(" · ".join(meta))

    if result.sql:
        st.code(result.sql, language="sql")

    if result.result is not None and result.result.rows:
        frame = result.result.to_dataframe()
        chart = numeric_bar_chart(frame)
        if chart is not None:
            table_tab, chart_tab = st.tabs(["Table", "Chart"])
            table_tab.dataframe(frame, use_container_width=True, hide_index=True)
            chart_tab.altair_chart(chart, use_container_width=True)
        else:
            st.dataframe(frame, use_container_width=True, hide_index=True)

    failed = [a for a in result.attempts if not a.succeeded]
    if failed:
        with st.expander(f"The agent corrected itself {len(failed)} time(s) — see how"):
            for index, attempt in enumerate(failed, start=1):
                st.markdown(f"**Attempt {index}**")
                if attempt.sql:
                    st.code(attempt.sql, language="sql")
                st.warning(attempt.error)

    st.divider()


def main() -> None:
    st.title("🗄️ Natural Language SQL Agent")
    st.caption(
        "Ask a question in plain English. The agent reads the schema, writes SQL, "
        "checks that it only reads data, runs it, and explains what came back."
    )

    try:
        settings = Settings.from_env()
    except ValueError as err:
        st.error(f"Configuration error: {err}")
        st.stop()

    with st.sidebar:
        st.header("Setup")
        provider = st.selectbox(
            "Model provider",
            ["ollama", "openai", "anthropic", "watsonx"],
            index=["ollama", "openai", "anthropic", "watsonx"].index(settings.provider),
            help="Credentials come from your .env file.",
        )
        model = st.text_input("Model", value=settings.model or "", placeholder="provider default")
        database_url = st.text_input("Database URL", value=settings.database_url)
        max_rows = st.slider("Max rows per query", 10, 1000, settings.max_rows, step=10)
        max_repairs = st.slider("Self-repair attempts", 0, 5, settings.max_repairs)

    try:
        agent, database, llm = get_agent(
            provider, model.strip() or None, database_url, max_rows, max_repairs
        )
        tables = database.table_names()
    except Exception as err:
        st.error(f"Could not start: {err}")
        st.info(
            "Check your `.env`. For the default local setup: run `python scripts/setup_data.py` "
            "to fetch the sample database, and make sure Ollama is running."
        )
        st.stop()

    if not tables:
        st.warning("The database has no tables. Run `python scripts/setup_data.py` first.")
        st.stop()

    with st.sidebar:
        st.success(f"{llm.name} · {llm.model}")
        st.caption(f"{database.dialect} · {len(tables)} tables")
        with st.expander("Schema"):
            st.code(database.schema_description(), language="sql")

    if "history" not in st.session_state:
        st.session_state.history = []
    if "pending" not in st.session_state:
        st.session_state.pending = None

    st.write("**Try one of these**")
    columns = st.columns(3)
    for index, example in enumerate(EXAMPLES):
        if columns[index % 3].button(example, use_container_width=True, key=f"ex{index}"):
            st.session_state.pending = example

    typed = st.chat_input("Ask a question about the data...")
    question = typed or st.session_state.pending
    st.session_state.pending = None

    if question:
        with st.spinner("Writing SQL and running it..."):
            st.session_state.history.insert(0, agent.ask(question))

    for result in st.session_state.history:
        render(result)


if __name__ == "__main__":
    main()
