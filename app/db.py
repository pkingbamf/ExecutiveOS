from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


ALTERS = {
    "users": [
        "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0",
    ],
    "projects": ["ALTER TABLE projects ADD COLUMN deleted_at TEXT"],
    "decisions": ["ALTER TABLE decisions ADD COLUMN deleted_at TEXT"],
    "action_items": ["ALTER TABLE action_items ADD COLUMN deleted_at TEXT"],
    "stakeholders": ["ALTER TABLE stakeholders ADD COLUMN deleted_at TEXT"],
    "project_stakeholders": ["ALTER TABLE project_stakeholders ADD COLUMN deleted_at TEXT"],
    "risk_issues": ["ALTER TABLE risk_issues ADD COLUMN deleted_at TEXT"],
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, statements in ALTERS.items():
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for stmt in statements:
            col = stmt.split("ADD COLUMN", 1)[1].strip().split()[0]
            if col not in cols:
                conn.execute(stmt)


def migrate() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        _ensure_columns(conn)


def row_to_dict(row: sqlite3.Row | None):
    if row is None:
        return None
    data = dict(row)
    for key in ["tags", "metadata"]:
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key])
            except Exception:
                pass
    return data
