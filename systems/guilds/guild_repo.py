"""
Guild repository — domain facade over WorldDatabase.
All SQL access goes through world_db methods; this module never imports sqlite3.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from systems.guilds.guild_models import GuildState, GuildMember, GuildRelation, GuildProject

if TYPE_CHECKING:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_models import FoundGuildIntent


def get_guild_state(world_db: "WorldDatabase", guild_id: str) -> GuildState | None:
    row = world_db.get_guild_state(guild_id)
    return GuildState(**row) if row else None


def get_all_guild_states(world_db: "WorldDatabase") -> list[GuildState]:
    return [GuildState(**r) for r in world_db.get_all_guild_states()]


def get_members(world_db: "WorldDatabase", guild_id: str) -> list[GuildMember]:
    return [GuildMember(**r) for r in world_db.get_guild_members(guild_id)]


def get_relation(world_db: "WorldDatabase", guild_id_1: str, guild_id_2: str) -> GuildRelation | None:
    row = world_db.get_guild_relation(guild_id_1, guild_id_2)
    return GuildRelation(**row) if row else None


def _first_rank_for_archetype(archetype: str) -> str:
    return {
        "combat":   "recruit",
        "stealth":  "initiate",
        "arcane":   "apprentice",
        "merchant": "trader",
    }.get(archetype, "recruit")


def found_guild(
    world_db: "WorldDatabase",
    intent: "FoundGuildIntent",
    template_id: str,
    turn: int,
    player_id: str | None = None,
) -> GuildState:
    """Create guild_state + guild_members rows atomically. Returns the new GuildState."""
    guild_id = f"guild_{uuid.uuid4().hex[:8]}"
    first_rank = _first_rank_for_archetype(intent.archetype)

    all_members: list[str] = []
    if player_id:
        all_members.append(player_id)
    all_members.extend(m for m in intent.initial_members if m != player_id)

    founder_entity_id = player_id or (intent.initial_members[0] if intent.initial_members else "")

    with world_db.transaction():
        world_db._conn.execute(
            """INSERT INTO guild_state
               (guild_id, template_id, name, founding_turn, founding_reason,
                founder_entity_id, headquarters_zone_id, archetype,
                tick_last_updated, is_player_founded)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                guild_id, template_id, intent.name, turn,
                intent.founding_reason, founder_entity_id,
                intent.zone_id, intent.archetype, turn,
                1 if player_id else 0,
            ),
        )
        leader_id = all_members[0] if all_members else ""
        for i, entity_id in enumerate(all_members):
            entity_type = "player" if entity_id == player_id else "npc"
            rank = "leader" if i == 0 else first_rank
            world_db._conn.execute(
                """INSERT INTO guild_members
                   (guild_id, entity_id, entity_type, rank_id, joined_turn, loyalty, ambition)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(guild_id, entity_id) DO NOTHING""",
                (guild_id, entity_id, entity_type, rank, turn, 75, 50),
            )
        if leader_id:
            world_db._conn.execute(
                "UPDATE guild_state SET current_leader_id = ? WHERE guild_id = ?",
                (leader_id, guild_id),
            )

    return get_guild_state(world_db, guild_id)


def split_guild(
    world_db: "WorldDatabase",
    parent_id: str,
    new_state: GuildState,
    supporter_ids: list[str],
    turn: int,
) -> None:
    """Move supporters into a new splinter guild and adjust parent stability."""
    with world_db.transaction():
        world_db._conn.execute(
            """INSERT INTO guild_state
               (guild_id, template_id, name, founding_turn, founding_reason,
                founder_entity_id, headquarters_zone_id, archetype,
                lifecycle_state, morale, stability, wealth, influence,
                current_focus, tick_last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_state.guild_id, new_state.template_id, new_state.name,
                turn, f"Splinter from {parent_id}",
                supporter_ids[0] if supporter_ids else "",
                new_state.headquarters_zone_id, new_state.archetype,
                "active", new_state.morale, new_state.stability,
                new_state.wealth, new_state.influence,
                new_state.current_focus, turn,
            ),
        )
        for entity_id in supporter_ids:
            parent_member = world_db.get_member(parent_id, entity_id)
            if parent_member:
                world_db._conn.execute(
                    "DELETE FROM guild_members WHERE guild_id = ? AND entity_id = ?",
                    (parent_id, entity_id),
                )
                world_db._conn.execute(
                    """INSERT INTO guild_members
                       (guild_id, entity_id, entity_type, rank_id, joined_turn, loyalty, ambition)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(guild_id, entity_id) DO NOTHING""",
                    (
                        new_state.guild_id, entity_id,
                        parent_member["entity_type"], "recruit",
                        turn, parent_member["loyalty"], parent_member["ambition"],
                    ),
                )
        if supporter_ids:
            world_db._conn.execute(
                "UPDATE guild_state SET current_leader_id = ? WHERE guild_id = ?",
                (supporter_ids[0], new_state.guild_id),
            )
        parent_row = world_db.get_guild_state(parent_id)
        if parent_row:
            world_db._conn.execute(
                "UPDATE guild_state SET stability = MAX(0, stability - 10) WHERE guild_id = ?",
                (parent_id,),
            )
        world_db._conn.execute(
            """INSERT INTO guild_relations
               (guild_id_1, guild_id_2, stance, trust, hostility, tension, last_changed_turn)
               VALUES (?, ?, 'hostile', 10, 70, 80, ?)
               ON CONFLICT(guild_id_1, guild_id_2) DO UPDATE SET
                 stance='hostile', trust=10, hostility=70, tension=80,
                 last_changed_turn=excluded.last_changed_turn""",
            (*sorted([new_state.guild_id, parent_id]), turn),
        )
