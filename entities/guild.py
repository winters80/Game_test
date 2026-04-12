"""
Guild entities — definitions loaded from JSON, membership tracked on Player model.

GuildRank       — a single rank within a guild
GuildDefinition — full guild definition with ranks, perks, and requirements
GuildRegistry   — loads all guild definitions from a JSON file
"""
from __future__ import annotations

import json
from pathlib import Path
from pydantic import BaseModel, Field


class GuildPerk(BaseModel):
    perk_id: str
    name: str
    description: str
    rank_required: str          # minimum rank to access this perk
    stat_bonuses: dict[str, int] = Field(default_factory=dict)  # {"STR": 2}
    skill_unlocks: list[str] = Field(default_factory=list)
    discount_pct: int = 0       # % discount at guild merchants
    special_flag: str = ""      # sets this flag on player when perk active


class GuildRank(BaseModel):
    rank_id: str
    name: str
    standing_required: float    # minimum standing to hold this rank
    title: str = ""             # how the guild addresses the player
    description: str = ""


class GuildDefinition(BaseModel):
    guild_id: str
    name: str
    description: str
    flavor_text: str = ""
    archetype: str              # "combat" | "stealth" | "arcane" | "merchant"
    zone_id: str                # primary zone/city where guild HQ is
    npc_contact_id: str = ""    # NPC to talk to for joining
    join_requires: dict = Field(default_factory=dict)   # {"min_stats": {"STR": 10}}
    rival_guilds: list[str] = Field(default_factory=list)
    allied_factions: list[str] = Field(default_factory=list)
    ranks: list[GuildRank] = Field(default_factory=list)
    perks: list[GuildPerk] = Field(default_factory=list)
    is_auction_guild: bool = False

    def get_rank(self, rank_id: str) -> GuildRank | None:
        for r in self.ranks:
            if r.rank_id == rank_id:
                return r
        return None

    def rank_for_standing(self, standing: float) -> GuildRank:
        """Return the highest rank the player qualifies for at given standing."""
        qualified = [r for r in self.ranks if r.standing_required <= standing]
        if not qualified:
            return self.ranks[0] if self.ranks else GuildRank(rank_id="outsider", name="Outsider", standing_required=-999)
        return max(qualified, key=lambda r: r.standing_required)

    def next_rank(self, current_rank_id: str) -> GuildRank | None:
        ids = [r.rank_id for r in self.ranks]
        if current_rank_id not in ids:
            return self.ranks[0] if self.ranks else None
        idx = ids.index(current_rank_id)
        return self.ranks[idx + 1] if idx + 1 < len(self.ranks) else None


class GuildRegistry:
    def __init__(self) -> None:
        self._guilds: dict[str, GuildDefinition] = {}

    def load_from_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            g = GuildDefinition.model_validate(entry)
            self._guilds[g.guild_id] = g

    def get(self, guild_id: str) -> GuildDefinition | None:
        return self._guilds.get(guild_id)

    def all(self) -> list[GuildDefinition]:
        return list(self._guilds.values())

    def auction_guilds(self) -> list[GuildDefinition]:
        return [g for g in self._guilds.values() if g.is_auction_guild]
