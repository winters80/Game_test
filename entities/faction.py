"""
Faction entities — definitions loaded from JSON, standing tracked in SQLite.
"""
from __future__ import annotations

import json
from pathlib import Path
from pydantic import BaseModel, Field


class FactionRank(BaseModel):
    rank_id: str
    name: str
    standing_required: float
    title: str = ""
    description: str = ""
    perks: list[str] = Field(default_factory=list)  # skill_ids or special flags


class FactionDefinition(BaseModel):
    faction_id: str
    name: str
    description: str
    flavor_text: str = ""
    ideology: str               # "order" | "chaos" | "balance" | "power" | "freedom"
    zone_id: str = ""           # primary zone of influence
    rival_factions: list[str] = Field(default_factory=list)
    allied_factions: list[str] = Field(default_factory=list)
    ranks: list[FactionRank] = Field(default_factory=list)
    join_requires: dict = Field(default_factory=dict)
    alignment_min: float = -100.0
    alignment_max: float = 100.0

    def rank_for_standing(self, standing: float) -> FactionRank:
        qualified = [r for r in self.ranks if r.standing_required <= standing]
        if not qualified:
            return FactionRank(rank_id="outsider", name="Outsider", standing_required=-999)
        return max(qualified, key=lambda r: r.standing_required)

    def get_rank(self, rank_id: str) -> FactionRank | None:
        for r in self.ranks:
            if r.rank_id == rank_id:
                return r
        return None


class FactionRegistry:
    def __init__(self) -> None:
        self._factions: dict[str, FactionDefinition] = {}

    def load_from_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            f = FactionDefinition.model_validate(entry)
            self._factions[f.faction_id] = f

    def get(self, faction_id: str) -> FactionDefinition | None:
        return self._factions.get(faction_id)

    def all(self) -> list[FactionDefinition]:
        return list(self._factions.values())
