"""world_actions + world_expansions — per-save state for AI world growth."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def get_action(conn: sqlite3.Connection, action_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM world_actions WHERE action_key = ?", (action_key,)
    ).fetchone()
    return dict(row) if row else None


def record_action(
    conn: sqlite3.Connection, action_key: str, verb: str, subject: str,
    zone_id: str, turn: int,
) -> dict[str, Any]:
    """Insert a first sighting (status 'pending') or bump ``times``. Returns the row."""
    conn.execute(
        """INSERT INTO world_actions (action_key, verb, subject, zone_id, first_turn)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(action_key) DO UPDATE SET times = times + 1""",
        (action_key, verb, subject, zone_id, turn),
    )
    conn.commit()
    return get_action(conn, action_key)  # type: ignore[return-value]


def set_status(conn: sqlite3.Connection, action_key: str, status: str) -> None:
    conn.execute(
        "UPDATE world_actions SET status = ? WHERE action_key = ?", (status, action_key)
    )
    conn.commit()


def set_last_yield_turn(conn: sqlite3.Connection, action_key: str, turn: int) -> None:
    conn.execute(
        "UPDATE world_actions SET last_yield_turn = ? WHERE action_key = ?", (turn, action_key)
    )
    conn.commit()


def list_actions(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM world_actions WHERE status = ? ORDER BY first_turn", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM world_actions ORDER BY first_turn").fetchall()
    return [dict(r) for r in rows]


def store_expansion(
    conn: sqlite3.Connection, action_key: str, bundle: dict,
    yield_item_id: str, turn: int,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO world_expansions
           (action_key, bundle, yield_item_id, created_turn) VALUES (?, ?, ?, ?)""",
        (action_key, json.dumps(bundle), yield_item_id, turn),
    )
    conn.execute(
        "UPDATE world_actions SET status = 'expanded' WHERE action_key = ?", (action_key,)
    )
    conn.commit()


def get_expansion(conn: sqlite3.Connection, action_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM world_expansions WHERE action_key = ?", (action_key,)
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["bundle"] = json.loads(out["bundle"])
    return out


def load_expansions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM world_expansions ORDER BY created_turn").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["bundle"] = json.loads(d["bundle"])
        out.append(d)
    return out
