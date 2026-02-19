import sqlite3
from datetime import datetime

DB_PATH = "query_logs.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            intent TEXT,
            status TEXT,
            confidence TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_query(query, intent, status, confidence):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO query_logs (query, intent, status, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
        (query, intent, status, confidence, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
