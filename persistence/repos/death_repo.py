"""Death record queries against a sqlite3.Connection."""
from __future__ import annotations

import sqlite3


def record_death(
    conn: sqlite3.Connection,
    cause: str, zone_id: str, turn_number: int,
    level_at_death: int, alignment_at_death: float, lives_remaining: int,
) -> None:
    conn.execute(
        """INSERT INTO death_records
           (cause, zone_id, turn_number, level_at_death, alignment_at_death, lives_remaining)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (cause, zone_id, turn_number, level_at_death, alignment_at_death, lives_remaining),
    )
    conn.commit()


def get_death_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) as c FROM death_records").fetchone()
    return row["c"] if row else 0
