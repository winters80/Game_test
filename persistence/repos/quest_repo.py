"""Quest-instance and AI-quest queries against a sqlite3.Connection."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def add_quest(
    conn: sqlite3.Connection,
    instance_id: str, template_id: str | None,
    initial_state: str, giver_npc_id: str | None,
    turn_number: int = 0, ai_context: dict | None = None,
) -> None:
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO quest_instances
               (instance_id, template_id, current_state, giver_npc_id,
                accepted_turn, ai_context)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                instance_id, template_id, initial_state, giver_npc_id,
                turn_number, json.dumps(ai_context or {}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def store_ai_quest(conn: sqlite3.Connection, instance_id: str, definition: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ai_quest_data VALUES (?, ?)",
        (instance_id, json.dumps(definition)),
    )
    conn.commit()


def get_ai_quest_definition(conn: sqlite3.Connection, instance_id: str) -> dict | None:
    """Return the stored AI-generated quest template JSON for this instance, or None."""
    row = conn.execute(
        "SELECT definition FROM ai_quest_data WHERE instance_id = ?",
        (instance_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["definition"])
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def get_quest(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM quest_instances WHERE instance_id = ?", (instance_id,)
    ).fetchone()
    return dict(row) if row else None


def get_active_quests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM quest_instances WHERE is_active = 1"
    ).fetchall()
    return [dict(r) for r in rows]


def advance_quest(conn: sqlite3.Connection, instance_id: str, new_state: str) -> None:
    conn.execute(
        "UPDATE quest_instances SET current_state = ? WHERE instance_id = ?",
        (new_state, instance_id),
    )
    conn.commit()


def complete_quest(conn: sqlite3.Connection, instance_id: str, outcome: str, turn_number: int) -> None:
    conn.execute(
        """UPDATE quest_instances
           SET is_active = 0, outcome = ?, completed_turn = ?
           WHERE instance_id = ?""",
        (outcome, turn_number, instance_id),
    )
    conn.commit()
