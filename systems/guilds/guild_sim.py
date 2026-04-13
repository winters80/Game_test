"""
Guild Simulation — background tick that selects agents, generates intents via AI,
and adjudicates them deterministically.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from systems.guilds import guild_engine, guild_repo
from systems.guilds.guild_models import GuildState, GuildMember, GuildIntentResult

if TYPE_CHECKING:
    from persistence.world_db import WorldDatabase
    from ai.content_generator import ContentGenerator
    from systems.guilds.guild_models import GuildIntent

MAX_AGENTS_PER_TICK = 3


def compute_betrayal_risk_all(world_db: "WorldDatabase") -> dict[tuple[str, str], float]:
    """Pre-compute betrayal risk for every member across all active guilds."""
    risks: dict[tuple[str, str], float] = {}
    for gs in guild_repo.get_all_guild_states(world_db):
        for member in guild_repo.get_members(world_db, gs.guild_id):
            risk = guild_engine.compute_betrayal_risk(member, gs)
            risks[(gs.guild_id, member.entity_id)] = risk
    return risks


def select_active_agents(
    world_db: "WorldDatabase",
    max_agents: int = MAX_AGENTS_PER_TICK,
) -> list[tuple[str, str]]:
    """
    Return up to max_agents (guild_id, entity_id) pairs.
    Leaders always get highest score; then members ranked by betrayal risk.
    """
    candidates: list[tuple[float, str, str]] = []
    risks = compute_betrayal_risk_all(world_db)

    for gs in guild_repo.get_all_guild_states(world_db):
        if gs.lifecycle_state == "collapsed":
            continue
        for member in guild_repo.get_members(world_db, gs.guild_id):
            is_leader = member.entity_id == gs.current_leader_id
            risk = risks.get((gs.guild_id, member.entity_id), 0.0)
            score = 100.0 if is_leader else risk
            candidates.append((score, gs.guild_id, member.entity_id))

    candidates.sort(reverse=True)
    return [(g, e) for _, g, e in candidates[:max_agents]]


def generate_npc_intent(
    guild: GuildState,
    member: GuildMember,
    ai_generator: "ContentGenerator | None",
) -> "GuildIntent | None":
    """Ask the AI for a structured guild intent for this NPC. Returns None if no AI."""
    if ai_generator is None:
        return None
    try:
        return ai_generator.generate_guild_intent(guild, member)
    except Exception:
        return None


def tick(
    world_db: "WorldDatabase",
    guild_registry: object,
    ai_generator: "ContentGenerator | None",
    turn: int,
    max_agents: int = MAX_AGENTS_PER_TICK,
) -> list[GuildIntentResult]:
    """
    One background simulation tick:
    1. Select eligible agents (leaders + high-risk members)
    2. For each, ask AI for an intent (skip if no AI)
    3. Adjudicate deterministically
    4. Update tick_last_updated on processed guilds
    """
    results: list[GuildIntentResult] = []

    for guild_id, entity_id in select_active_agents(world_db, max_agents):
        guild = guild_repo.get_guild_state(world_db, guild_id)
        if not guild:
            continue
        members = guild_repo.get_members(world_db, guild_id)
        member = next((m for m in members if m.entity_id == entity_id), None)
        if not member:
            continue

        intent = generate_npc_intent(guild, member, ai_generator)
        if intent:
            result = guild_engine.adjudicate_intent(intent, guild, members, world_db, turn)
            results.append(result)

        world_db.update_guild_state(guild_id, tick_last_updated=turn)

    return results
