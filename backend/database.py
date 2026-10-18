import sqlite3
from datetime import datetime

DB_FILE = "support_agent.db"


def init_db():
    """Initialize database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            message TEXT,
            sender TEXT,
            timestamp TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            issue TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        )
    ''')

    conn.commit()
    conn.close()


def save_message(session_id: str, message: str, sender: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversations (session_id, message, sender, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, message, sender, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_conversation_history(session_id: str, limit: int = 10):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT message, sender FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    )
    messages = cursor.fetchall()
    conn.close()
    return list(reversed(messages))


def create_ticket(session_id: str, issue: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tickets (session_id, issue, created_at) VALUES (?, ?, ?)",
        (session_id, issue, datetime.now().isoformat())
    )
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return ticket_id


init_db()
