"""Bot agent persistence queries against a sqlite3.Connection."""
from __future__ import annotations

import json
import sqlite3


def upsert_bot_instance(
    conn: sqlite3.Connection,
    bot_id: str, definition: dict, current_zone_id: str, last_active_turn: int,
) -> None:
    """Save or update a bot agent's state."""
    conn.execute(
        """INSERT INTO bot_instances (bot_id, definition, current_zone_id, last_active_turn)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(bot_id) DO UPDATE SET
             definition = excluded.definition,
             current_zone_id = excluded.current_zone_id,
             last_active_turn = excluded.last_active_turn""",
        (bot_id, json.dumps(definition), current_zone_id, last_active_turn),
    )
    conn.commit()


def load_bot_instances(conn: sqlite3.Connection) -> list[dict]:
    """Load all bot agent rows as plain dicts."""
    rows = conn.execute("SELECT * FROM bot_instances").fetchall()
    return [dict(r) for r in rows]
