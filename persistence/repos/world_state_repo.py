"""World flags + turn log queries against a sqlite3.Connection."""
from __future__ import annotations

import json
import sqlite3


# ── World Flags ──────────────────────────────────────────────────────────────

def set_world_flag(conn: sqlite3.Connection, key: str, value: str = "true", turn: int = 0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO world_flags VALUES (?, ?, ?)",
        (key, value, turn),
    )
    conn.commit()


def get_world_flag(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT flag_value FROM world_flags WHERE flag_key = ?", (key,)
    ).fetchone()
    return row["flag_value"] if row else None


def has_world_flag(conn: sqlite3.Connection, key: str) -> bool:
    return get_world_flag(conn, key) is not None


# ── Turn Log ─────────────────────────────────────────────────────────────────

def log_event(conn: sqlite3.Connection, turn_number: int, event_type: str, payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO turn_log (turn_number, event_type, payload) VALUES (?, ?, ?)",
        (turn_number, event_type, json.dumps(payload or {})),
    )
    conn.commit()
