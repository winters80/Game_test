"""Guild persistence queries: state + members + relations + projects."""
from __future__ import annotations

import sqlite3


# ── Guild State ──────────────────────────────────────────────────────────────

def add_guild_state(
    conn: sqlite3.Connection,
    guild_id: str, template_id: str, name: str, founding_turn: int,
    headquarters_zone_id: str, archetype: str,
    founder_entity_id: str = "", founding_reason: str = "",
    is_player_founded: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO guild_state
           (guild_id, template_id, name, founding_turn, founding_reason,
            founder_entity_id, headquarters_zone_id, archetype,
            tick_last_updated, is_player_founded)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (guild_id, template_id, name, founding_turn, founding_reason,
         founder_entity_id, headquarters_zone_id, archetype,
         founding_turn, is_player_founded),
    )
    conn.commit()


def get_guild_state(conn: sqlite3.Connection, guild_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM guild_state WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_guild_states(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM guild_state").fetchall()
    return [dict(r) for r in rows]


def update_guild_state(conn: sqlite3.Connection, guild_id: str, **fields) -> None:
    """Update any subset of guild_state fields. Uses keyword arguments."""
    if not fields:
        return
    allowed = {
        "name", "current_leader_id", "archetype", "lifecycle_state",
        "morale", "stability", "influence", "secrecy", "wealth",
        "current_focus", "tick_last_updated", "is_hidden",
        "founding_reason", "founder_entity_id",
    }
    set_parts = []
    values = []
    for k, v in fields.items():
        if k in allowed:
            set_parts.append(f"{k} = ?")
            values.append(v)
    if not set_parts:
        return
    values.append(guild_id)
    conn.execute(
        f"UPDATE guild_state SET {', '.join(set_parts)} WHERE guild_id = ?",
        values,
    )
    conn.commit()


# ── Guild Members ────────────────────────────────────────────────────────────

def add_guild_member(
    conn: sqlite3.Connection,
    guild_id: str, entity_id: str, entity_type: str,
    rank_id: str, joined_turn: int,
    loyalty: int = 50, ambition: int = 50,
) -> None:
    conn.execute(
        """INSERT INTO guild_members
           (guild_id, entity_id, entity_type, rank_id, joined_turn, loyalty, ambition)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(guild_id, entity_id) DO NOTHING""",
        (guild_id, entity_id, entity_type, rank_id, joined_turn, loyalty, ambition),
    )
    conn.commit()


def get_guild_members(conn: sqlite3.Connection, guild_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM guild_members WHERE guild_id = ?", (guild_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_member(conn: sqlite3.Connection, guild_id: str, entity_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM guild_members WHERE guild_id = ? AND entity_id = ?",
        (guild_id, entity_id),
    ).fetchone()
    return dict(row) if row else None


def update_member(conn: sqlite3.Connection, guild_id: str, entity_id: str, **fields) -> None:
    allowed = {"rank_id", "standing", "loyalty", "ambition", "last_promotion_turn"}
    set_parts = []
    values = []
    for k, v in fields.items():
        if k in allowed:
            set_parts.append(f"{k} = ?")
            values.append(v)
    if not set_parts:
        return
    values.extend([guild_id, entity_id])
    conn.execute(
        f"UPDATE guild_members SET {', '.join(set_parts)} WHERE guild_id = ? AND entity_id = ?",
        values,
    )
    conn.commit()


def remove_guild_member(conn: sqlite3.Connection, guild_id: str, entity_id: str) -> None:
    conn.execute(
        "DELETE FROM guild_members WHERE guild_id = ? AND entity_id = ?",
        (guild_id, entity_id),
    )
    conn.commit()


# ── Guild Relations ──────────────────────────────────────────────────────────

def set_guild_relation(
    conn: sqlite3.Connection,
    guild_id_1: str, guild_id_2: str, stance: str = "neutral",
    trust: int = 50, hostility: int = 0, tension: int = 0, turn: int = 0,
) -> None:
    a, b = sorted([guild_id_1, guild_id_2])
    conn.execute(
        """INSERT INTO guild_relations
           (guild_id_1, guild_id_2, stance, trust, hostility, tension, last_changed_turn)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(guild_id_1, guild_id_2) DO UPDATE SET
             stance = excluded.stance,
             trust = excluded.trust,
             hostility = excluded.hostility,
             tension = excluded.tension,
             last_changed_turn = excluded.last_changed_turn""",
        (a, b, stance, trust, hostility, tension, turn),
    )
    conn.commit()


def get_guild_relation(conn: sqlite3.Connection, guild_id_1: str, guild_id_2: str) -> dict | None:
    a, b = sorted([guild_id_1, guild_id_2])
    row = conn.execute(
        "SELECT * FROM guild_relations WHERE guild_id_1 = ? AND guild_id_2 = ?",
        (a, b),
    ).fetchone()
    return dict(row) if row else None


def update_guild_relation(conn: sqlite3.Connection, guild_id_1: str, guild_id_2: str, **fields) -> None:
    a, b = sorted([guild_id_1, guild_id_2])
    allowed = {"stance", "trust", "hostility", "tension", "last_changed_turn"}
    set_parts = []
    values = []
    for k, v in fields.items():
        if k in allowed:
            set_parts.append(f"{k} = ?")
            values.append(v)
    if not set_parts:
        return
    values.extend([a, b])
    conn.execute(
        f"UPDATE guild_relations SET {', '.join(set_parts)} WHERE guild_id_1 = ? AND guild_id_2 = ?",
        values,
    )
    conn.commit()


# ── Guild Projects ───────────────────────────────────────────────────────────

def add_guild_project(
    conn: sqlite3.Connection,
    project_id: str, guild_id: str, project_type: str,
    started_turn: int, target_id: str = "", risk: int = 10,
    lead_entity_id: str = "",
) -> None:
    conn.execute(
        """INSERT INTO guild_projects
           (project_id, guild_id, project_type, target_id, risk,
            lead_entity_id, started_turn)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (project_id, guild_id, project_type, target_id, risk,
         lead_entity_id, started_turn),
    )
    conn.commit()


def get_guild_projects(conn: sqlite3.Connection, guild_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM guild_projects WHERE guild_id = ?", (guild_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM guild_projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    return dict(row) if row else None


def update_project(conn: sqlite3.Connection, project_id: str, progress: int) -> None:
    conn.execute(
        "UPDATE guild_projects SET progress = ? WHERE project_id = ?",
        (progress, project_id),
    )
    conn.commit()


def complete_project(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        "DELETE FROM guild_projects WHERE project_id = ?", (project_id,)
    )
    conn.commit()
