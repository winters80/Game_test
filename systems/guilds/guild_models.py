"""
Guild runtime models (Pydantic v2).
Separate from entities/guild.py which holds the immutable template models.
These models represent live world state stored in SQLite.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field


# ── Runtime state models ──────────────────────────────────────────────────────

class GuildState(BaseModel):
    guild_id: str
    template_id: str
    name: str
    founding_turn: int
    founding_reason: str = ""
    founder_entity_id: str = ""
    headquarters_zone_id: str
    current_leader_id: str = ""
    archetype: str
    lifecycle_state: Literal["active", "fragmenting", "declining", "dormant", "collapsed"] = "active"
    morale: int = 50
    stability: int = 50
    influence: int = 10
    secrecy: int = 50
    wealth: int = 0
    current_focus: str = "idle"
    tick_last_updated: int = 0
    is_player_founded: bool = False
    is_hidden: bool = False


class GuildMember(BaseModel):
    guild_id: str
    entity_id: str
    entity_type: Literal["npc", "player"]
    rank_id: str
    standing: int = 0
    loyalty: int = 50
    ambition: int = 50
    joined_turn: int
    last_promotion_turn: int | None = None


class GuildRelation(BaseModel):
    guild_id_1: str
    guild_id_2: str
    stance: Literal["allied", "neutral", "hostile"] = "neutral"
    trust: int = 50
    hostility: int = 0
    tension: int = 0
    last_changed_turn: int = 0


class GuildProject(BaseModel):
    project_id: str
    guild_id: str
    project_type: Literal[
        "recruitment", "raid", "trade_route", "research",
        "sabotage", "coup", "craft_contract",
    ]
    target_id: str = ""
    progress: int = 0
    risk: int = 10
    lead_entity_id: str = ""
    started_turn: int


# ── Intent models (structured AI/player output) ───────────────────────────────

class FoundGuildIntent(BaseModel):
    intent: Literal["found_guild"] = "found_guild"
    name: str
    archetype: Literal["combat", "stealth", "arcane", "merchant"]
    zone_id: str
    founding_reason: str = ""
    initial_members: list[str] = Field(default_factory=list)   # npc_ids
    seed_traits: list[str] = Field(default_factory=list)


class LeakSecretsIntent(BaseModel):
    intent: Literal["leak_secrets"] = "leak_secrets"
    actor_id: str
    guild_id: str
    target_guild_id: str
    severity: int = 20    # 0-100


class StealResourcesIntent(BaseModel):
    intent: Literal["steal_resources"] = "steal_resources"
    actor_id: str
    guild_id: str
    amount: int = 50


class SabotageProjectIntent(BaseModel):
    intent: Literal["sabotage_project"] = "sabotage_project"
    actor_id: str
    guild_id: str
    project_id: str


class AttemptCoupIntent(BaseModel):
    intent: Literal["attempt_coup"] = "attempt_coup"
    actor_id: str
    guild_id: str


class FoundSplinterGuildIntent(BaseModel):
    intent: Literal["found_splinter_guild"] = "found_splinter_guild"
    actor_id: str
    parent_guild_id: str
    name: str
    initial_supporters: list[str] = Field(default_factory=list)
    new_focus: str = "idle"


GuildIntent = Annotated[
    Union[
        FoundGuildIntent,
        LeakSecretsIntent,
        StealResourcesIntent,
        SabotageProjectIntent,
        AttemptCoupIntent,
        FoundSplinterGuildIntent,
    ],
    Field(discriminator="intent"),
]


class GuildIntentResult(BaseModel):
    """Result of adjudicating a guild intent. Always produced by the engine, never AI."""
    intent: str
    success: bool
    narrative: str
    state_changes: dict = Field(default_factory=dict)
    new_guild_id: str | None = None
