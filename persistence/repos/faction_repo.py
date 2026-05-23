"""Faction standing and faction-relation queries against a sqlite3.Connection."""
from __future__ import annotations

import sqlite3


def get_faction_standing(conn: sqlite3.Connection, faction_id: str) -> tuple[float, str]:
    row = conn.execute(
        "SELECT standing, rank FROM faction_standing WHERE faction_id = ?",
        (faction_id,),
    ).fetchone()
    return (row["standing"], row["rank"]) if row else (0.0, "outsider")


def update_faction_standing(conn: sqlite3.Connection, faction_id: str, delta: float) -> None:
    conn.execute(
        """INSERT INTO faction_standing (faction_id, standing)
           VALUES (?, ?)
           ON CONFLICT(faction_id) DO UPDATE SET
             standing = MAX(-100.0, MIN(100.0, standing + excluded.standing))""",
        (faction_id, delta),
    )
    conn.commit()


def set_faction_rank(conn: sqlite3.Connection, faction_id: str, rank: str) -> None:
    conn.execute(
        """INSERT INTO faction_standing (faction_id, rank)
           VALUES (?, ?)
           ON CONFLICT(faction_id) DO UPDATE SET rank = excluded.rank""",
        (faction_id, rank),
    )
    conn.commit()


def get_faction_relation(conn: sqlite3.Connection, faction_a: str, faction_b: str) -> float:
    a, b = sorted([faction_a, faction_b])
    row = conn.execute(
        "SELECT relation FROM faction_relations WHERE faction_a = ? AND faction_b = ?",
        (a, b),
    ).fetchone()
    return row["relation"] if row else 0.0


def update_faction_relation(conn: sqlite3.Connection, faction_a: str, faction_b: str, delta: float) -> None:
    a, b = sorted([faction_a, faction_b])
    conn.execute(
        """INSERT INTO faction_relations (faction_a, faction_b, relation)
           VALUES (?, ?, ?)
           ON CONFLICT(faction_a, faction_b) DO UPDATE SET
             relation = MAX(-100.0, MIN(100.0, relation + excluded.relation))""",
        (a, b, delta),
    )
    conn.commit()


def get_all_faction_relations(conn: sqlite3.Connection) -> list[dict]:
    """Return all faction-to-faction relation rows."""
    rows = conn.execute(
        "SELECT faction_a, faction_b, relation FROM faction_relations"
    ).fetchall()
    return [dict(r) for r in rows]


def set_faction_relation(conn: sqlite3.Connection, faction_a: str, faction_b: str, relation: float) -> None:
    """Absolute-set the relation between two factions (clamped -100..+100)."""
    a, b = sorted([faction_a, faction_b])
    clamped = max(-100.0, min(100.0, relation))
    conn.execute(
        """INSERT INTO faction_relations (faction_a, faction_b, relation)
           VALUES (?, ?, ?)
           ON CONFLICT(faction_a, faction_b) DO UPDATE SET relation = excluded.relation""",
        (a, b, clamped),
    )
    conn.commit()
