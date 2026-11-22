"""dungeon_lairs + lair_history — per-save boss succession state."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def upsert_lair(conn: sqlite3.Connection, lair_id: str, state: dict, turn: int) -> None:
    conn.execute(
        """INSERT INTO dungeon_lairs (lair_id, state, updated_turn) VALUES (?, ?, ?)
           ON CONFLICT(lair_id) DO UPDATE SET state = excluded.state,
                                              updated_turn = excluded.updated_turn""",
        (lair_id, json.dumps(state), turn),
    )
    conn.commit()


def load_lairs(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT lair_id, state FROM dungeon_lairs").fetchall()
    return {r["lair_id"]: json.loads(r["state"]) for r in rows}


_HISTORY_COLS = (
    "lair_id", "generation", "occupant_id", "occupant_name", "killer_kind", "killer_name",
    "killer_level", "killed_by", "aftermath", "successor_id", "zone_id", "turn",
)


def add_history(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        f"INSERT INTO lair_history ({', '.join(_HISTORY_COLS)}) "
        f"VALUES ({', '.join('?' for _ in _HISTORY_COLS)})",
        tuple(row.get(c, "") for c in _HISTORY_COLS),
    )
    conn.commit()


def lair_history(conn: sqlite3.Connection, lair_id: str | None = None) -> list[dict[str, Any]]:
    if lair_id:
        rows = conn.execute(
            "SELECT * FROM lair_history WHERE lair_id = ? ORDER BY id", (lair_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM lair_history ORDER BY id").fetchall()
    return [dict(r) for r in rows]
