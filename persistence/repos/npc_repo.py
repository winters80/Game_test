"""NPC and NPC-memory queries against a sqlite3.Connection."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def upsert_npc(conn: sqlite3.Connection, npc_id: str, template_id: str, zone_id: str, role: str) -> None:
    conn.execute(
        """INSERT INTO npc_instances (npc_id, template_id, current_zone_id, current_role)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(npc_id) DO NOTHING""",
        (npc_id, template_id, zone_id, role),
    )
    conn.commit()


def get_npc(conn: sqlite3.Connection, npc_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM npc_instances WHERE npc_id = ?", (npc_id,)
    ).fetchone()
    return dict(row) if row else None


def update_npc_disposition(conn: sqlite3.Connection, npc_id: str, delta: float) -> None:
    conn.execute(
        """UPDATE npc_instances
           SET disposition = MAX(-100.0, MIN(100.0, disposition + ?))
           WHERE npc_id = ?""",
        (delta, npc_id),
    )
    conn.commit()


def set_npc_state(conn: sqlite3.Connection, npc_id: str, key: str, value: Any) -> None:
    """Set a key in the NPC's custom_state JSON blob."""
    row = conn.execute(
        "SELECT custom_state FROM npc_instances WHERE npc_id = ?", (npc_id,)
    ).fetchone()
    if not row:
        return
    state = json.loads(row["custom_state"] or "{}")
    state[key] = value
    conn.execute(
        "UPDATE npc_instances SET custom_state = ? WHERE npc_id = ?",
        (json.dumps(state), npc_id),
    )
    conn.commit()


def add_npc_memory(
    conn: sqlite3.Connection,
    npc_id: str, event_type: str, summary: str,
    turn_number: int = 0, alignment_snapshot: float = 0.0,
) -> None:
    conn.execute(
        """INSERT INTO npc_memory
           (npc_id, event_type, summary, turn_number, alignment_snapshot)
           VALUES (?, ?, ?, ?, ?)""",
        (npc_id, event_type, summary, turn_number, alignment_snapshot),
    )
    conn.commit()


def get_npc_memory(conn: sqlite3.Connection, npc_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM npc_memory WHERE npc_id = ?
           ORDER BY id DESC LIMIT ?""",
        (npc_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def count_npc_memory(conn: sqlite3.Connection, npc_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) as c FROM npc_memory WHERE npc_id = ? AND is_compressed = 0",
        (npc_id,),
    ).fetchone()
    return row["c"] if row else 0


def compress_npc_memory(conn: sqlite3.Connection, npc_id: str, summary: str, up_to_id: int) -> None:
    """Replace memory rows up to up_to_id with a single compressed summary."""
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM npc_memory WHERE npc_id = ? AND id <= ? AND is_compressed = 0",
            (npc_id, up_to_id),
        )
        conn.execute(
            """INSERT INTO npc_memory
               (npc_id, event_type, summary, is_compressed)
               VALUES (?, 'compressed_history', ?, 1)""",
            (npc_id, summary),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
