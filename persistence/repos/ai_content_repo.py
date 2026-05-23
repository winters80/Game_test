"""AI-generated content queries: bg_content + ai_skills + world_events streams."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


# ── Background-generated content ─────────────────────────────────────────────

def store_bg_content(
    conn: sqlite3.Connection,
    content_type: str, content_id: str, definition: dict,
    zone_id: str | None = None, generated_turn: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO ai_generated_content
           (content_type, content_id, definition, zone_id, generated_turn)
           VALUES (?, ?, ?, ?, ?)""",
        (content_type, content_id, json.dumps(definition), zone_id, generated_turn),
    )
    conn.commit()


def get_unintegrated_bg_content(conn: sqlite3.Connection, content_type: str | None = None) -> list[dict[str, Any]]:
    if content_type:
        rows = conn.execute(
            "SELECT * FROM ai_generated_content WHERE integrated = 0 AND content_type = ?",
            (content_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ai_generated_content WHERE integrated = 0"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_bg_content_integrated(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute(
        "UPDATE ai_generated_content SET integrated = 1 WHERE id = ?",
        (row_id,),
    )
    conn.commit()


# ── AI-generated skills ──────────────────────────────────────────────────────

def store_ai_skill(conn: sqlite3.Connection, skill_id: str, definition: dict, source: str, generated_turn: int) -> None:
    """Persist an AI-generated skill definition."""
    conn.execute(
        "INSERT OR REPLACE INTO ai_generated_skills VALUES (?, ?, ?, ?)",
        (skill_id, json.dumps(definition), source, generated_turn),
    )
    conn.commit()


def load_ai_skills(conn: sqlite3.Connection) -> list[dict]:
    """Load all AI-generated skill definitions as raw dicts."""
    rows = conn.execute("SELECT definition FROM ai_generated_skills").fetchall()
    return [json.loads(r["definition"]) for r in rows]


# ── World events stream (rumours, lore, area activity, etc.) ─────────────────

def store_world_event(
    conn: sqlite3.Connection,
    event_type: str,
    event_text: str,
    zone_id: str | None = None,
    title: str = "",
    npc_hint: str = "",
    generated_turn: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO world_events
           (event_type, zone_id, event_text, title, npc_hint, generated_turn)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event_type, zone_id, event_text, title, npc_hint, generated_turn),
    )
    conn.commit()


def get_unshown_events(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM world_events WHERE shown = 0 ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_events_shown(conn: sqlite3.Connection, ids: list[int]) -> None:
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE world_events SET shown = 1 WHERE id IN ({placeholders})", ids
        )
        conn.commit()


def get_recent_events(conn: sqlite3.Connection, limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
    if event_type:
        rows = conn.execute(
            "SELECT * FROM world_events WHERE event_type = ? ORDER BY id DESC LIMIT ?",
            (event_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM world_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
