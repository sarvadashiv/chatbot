import sqlite3
from datetime import datetime

DB_PATH = "query_logs.db"


def _current_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            intent TEXT,
            status TEXT,
            created_at TEXT
        )
        """
    )

    cols = _current_columns(c, "query_logs")
    if "confidence" in cols:
        c.execute(
            """
            CREATE TABLE query_logs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                intent TEXT,
                status TEXT,
                created_at TEXT
            )
            """
        )
        c.execute(
            """
            INSERT INTO query_logs_new (id, query, intent, status, created_at)
            SELECT id, query, intent, status, created_at
            FROM query_logs
            """
        )
        c.execute("DROP TABLE query_logs")
        c.execute("ALTER TABLE query_logs_new RENAME TO query_logs")

    conn.commit()
    conn.close()


def log_query(query: str, intent: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO query_logs (query, intent, status, created_at) VALUES (?, ?, ?, ?)",
        (query, intent, status, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
