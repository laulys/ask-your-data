"""Ask a SQL database questions in plain English."""

from nlsql.agent import AgentResult, SQLAgent
from nlsql.config import Settings
from nlsql.database import Database
from nlsql.safety import UnsafeSQLError, enforce_read_only

__all__ = [
    "AgentResult",
    "Database",
    "SQLAgent",
    "Settings",
    "UnsafeSQLError",
    "enforce_read_only",
]

__version__ = "0.1.0"
