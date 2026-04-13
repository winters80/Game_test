"""
Guild Engine — deterministic adjudication of guild intents.
No AI calls. No randomness. The engine owns truth.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from systems.guilds.guild_models import (
    GuildState, GuildMember, GuildIntentResult,
    AttemptCoupIntent, FoundSplinterGuildIntent,
    LeakSecretsIntent, StealResourcesIntent, SabotageProjectIntent,
    FoundGuildIntent,
)

if TYPE_CHECKING:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_models import GuildIntent


def compute_betrayal_risk(member: GuildMember, guild: GuildState) -> float:
    """
    betrayal_risk = (100 - loyalty) * 0.5 + ambition * 0.3 + (50 - stability) * 0.2
    Clamped to [0, 100].
    """
    risk = (
        (100 - member.loyalty) * 0.5
        + member.ambition * 0.3
        + (50 - guild.stability) * 0.2
    )
    return max(0.0, min(100.0, risk))


def get_eligible_actions(risk: float) -> list[str]:
    """Return intent type strings this member may propose at this risk level."""
    actions: list[str] = []
    if risk >= 60:
        actions += ["steal_resources", "leak_secrets"]
    if risk >= 75:
        actions += ["attempt_coup"]
    if risk >= 85:
        actions += ["found_splinter_guild"]
    return actions


def adjudicate_intent(
    intent: "GuildIntent",
    guild: GuildState,
    members: list[GuildMember],
    world_db: "WorldDatabase",
    turn: int,
) -> GuildIntentResult:
    """Route intent to the correct adjudicator. Always returns a GuildIntentResult."""
    t = intent.intent
    if t == "found_guild":
        return _adj_found_guild(intent, world_db, turn)
    elif t == "attempt_coup":
        return _adj_coup(intent, guild, members, world_db, turn)
    elif t == "found_splinter_guild":
        return _adj_splinter(intent, guild, members, world_db, turn)
    elif t == "leak_secrets":
        return _adj_leak(intent, guild, world_db, turn)
    elif t == "steal_resources":
        return _adj_steal(intent, guild, world_db, turn)
    elif t == "sabotage_project":
        return _adj_sabotage(intent, guild, world_db, turn)
    return GuildIntentResult(
        intent=t, success=False, narrative="Unknown intent type.",
    )


def _adj_found_guild(intent: FoundGuildIntent, world_db: "WorldDatabase", turn: int) -> GuildIntentResult:
    """Founding is handled upstream by guild_repo.found_guild(); this is a passthrough."""
    return GuildIntentResult(
        intent="found_guild", success=True,
        narrative=f"The guild '{intent.name}' has been established.",
    )


def _adj_coup(
    intent: AttemptCoupIntent,
    guild: GuildState,
    members: list[GuildMember],
    world_db: "WorldDatabase",
    turn: int,
) -> GuildIntentResult:
    """
    supporters = members with loyalty < 40 (excluding actor)
    defenders  = members with loyalty >= 60
    power    = len(supporters) * (avg_ambition_supporters / 50)
    defense  = len(defenders) * (guild.morale / 50)
    power > defense → coup succeeds
    """
    supporters = [m for m in members if m.loyalty < 40 and m.entity_id != intent.actor_id]
    defenders  = [m for m in members if m.loyalty >= 60]
    avg_ambition = (sum(m.ambition for m in supporters) / len(supporters)) if supporters else 50.0
    power   = len(supporters) * (avg_ambition / 50.0)
    defense = len(defenders) * (guild.morale / 50.0)

    if power > defense:
        world_db.update_guild_state(
            intent.guild_id,
            current_leader_id=intent.actor_id,
            morale=max(0, guild.morale - 15),
            stability=max(0, guild.stability - 10),
            tick_last_updated=turn,
        )
        return GuildIntentResult(
            intent="attempt_coup",
            success=True,
            narrative=f"{intent.actor_id} has seized leadership of {guild.name}.",
            state_changes={"current_leader_id": intent.actor_id},
        )
    else:
        actor_member = world_db.get_member(intent.guild_id, intent.actor_id)
        if actor_member:
            world_db.update_member(
                intent.guild_id, intent.actor_id,
                standing=max(0, actor_member["standing"] - 20),
                loyalty=min(100, actor_member["loyalty"] + 10),
            )
        return GuildIntentResult(
            intent="attempt_coup",
            success=False,
            narrative=f"{intent.actor_id}'s coup attempt failed. The guild holds.",
        )


def _adj_splinter(
    intent: FoundSplinterGuildIntent,
    guild: GuildState,
    members: list[GuildMember],
    world_db: "WorldDatabase",
    turn: int,
) -> GuildIntentResult:
    """
    Prerequisites: stability < 35 OR max_tension > 70,
    supporter count meets archetype minimum,
    wealth >= 100 OR influence >= 20.
    """
    min_supporters = {"combat": 3, "stealth": 2, "arcane": 2, "merchant": 2}
    required = min_supporters.get(guild.archetype, 2)
    supporters = [
        m for m in members
        if m.entity_id in intent.initial_supporters and m.loyalty < 60
    ]

    # Check max tension across all relations
    all_states = world_db.get_all_guild_states()
    max_tension = 0
    for other in all_states:
        if other["guild_id"] != guild.guild_id:
            rel = world_db.get_guild_relation(guild.guild_id, other["guild_id"])
            if rel:
                max_tension = max(max_tension, rel["tension"])

    if guild.stability >= 35 and max_tension <= 70:
        return GuildIntentResult(
            intent="found_splinter_guild",
            success=False,
            narrative="The guild is too stable for a splinter faction to take root.",
        )
    if len(supporters) < required:
        return GuildIntentResult(
            intent="found_splinter_guild",
            success=False,
            narrative=f"Not enough supporters. Need {required}, found {len(supporters)}.",
        )
    if guild.wealth < 100 and guild.influence < 20:
        return GuildIntentResult(
            intent="found_splinter_guild",
            success=False,
            narrative="The splinter faction lacks the resources to establish itself.",
        )

    import uuid
    new_guild_id = f"splinter_{uuid.uuid4().hex[:6]}"
    new_state = GuildState(
        guild_id=new_guild_id,
        template_id=guild.template_id,
        name=intent.name,
        founding_turn=turn,
        headquarters_zone_id=guild.headquarters_zone_id,
        archetype=guild.archetype,
        current_focus=intent.new_focus,
        morale=60,
        stability=40,
        wealth=guild.wealth // 4,
        influence=guild.influence // 4,
    )
    from systems.guilds import guild_repo
    supporter_ids = [s.entity_id for s in supporters]
    guild_repo.split_guild(world_db, guild.guild_id, new_state, supporter_ids, turn)

    return GuildIntentResult(
        intent="found_splinter_guild",
        success=True,
        narrative=f"{intent.name} splinters from {guild.name}.",
        new_guild_id=new_guild_id,
        state_changes={"splinter_created": new_guild_id},
    )


def _adj_leak(
    intent: LeakSecretsIntent,
    guild: GuildState,
    world_db: "WorldDatabase",
    turn: int,
) -> GuildIntentResult:
    target = world_db.get_guild_state(intent.target_guild_id)
    if target:
        world_db.update_guild_state(
            intent.target_guild_id,
            secrecy=max(0, target["secrecy"] - intent.severity // 2),
        )
    rel = world_db.get_guild_relation(guild.guild_id, intent.target_guild_id)
    new_tension = min(100, (rel["tension"] if rel else 0) + intent.severity)
    world_db.update_guild_relation(
        guild.guild_id, intent.target_guild_id,
        tension=new_tension, last_changed_turn=turn,
    )
    return GuildIntentResult(
        intent="leak_secrets",
        success=True,
        narrative=f"Sensitive information about {intent.target_guild_id} has been leaked.",
        state_changes={"target_secrecy_reduced": intent.severity // 2},
    )


def _adj_steal(
    intent: StealResourcesIntent,
    guild: GuildState,
    world_db: "WorldDatabase",
    turn: int,
) -> GuildIntentResult:
    actual = min(intent.amount, guild.wealth)
    world_db.update_guild_state(
        intent.guild_id,
        wealth=guild.wealth - actual,
        stability=max(0, guild.stability - 5),
        tick_last_updated=turn,
    )
    return GuildIntentResult(
        intent="steal_resources",
        success=actual > 0,
        narrative=f"{intent.actor_id} stole {actual} wealth from {guild.name}.",
        state_changes={"wealth_stolen": actual},
    )


def _adj_sabotage(
    intent: SabotageProjectIntent,
    guild: GuildState,
    world_db: "WorldDatabase",
    turn: int,
) -> GuildIntentResult:
    project = world_db.get_project(intent.project_id)
    if not project:
        return GuildIntentResult(
            intent="sabotage_project",
            success=False,
            narrative="Project not found.",
        )
    new_progress = max(0, project["progress"] - 30)
    world_db.update_project(intent.project_id, new_progress)
    return GuildIntentResult(
        intent="sabotage_project",
        success=True,
        narrative=f"Project {intent.project_id} sabotaged — progress set back by 30.",
        state_changes={"project_progress_reduced": 30},
    )
