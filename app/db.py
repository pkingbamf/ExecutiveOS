from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def migrate() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


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
