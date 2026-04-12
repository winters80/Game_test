from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from entities.player import Stats
from entities.enums import SpeciesEvolutionTrigger


class EvolutionStage(BaseModel):
    stage: int
    name: str                              # "Awakened Elf", "Ascendant Elf"
    description: str
    flavor_text: str = ""
    stat_bonus: Stats = Stats(STR=0, INT=0, AGI=0, LCK=0, VIT=0, WIS=0, END=0)
    perception_bonus: int = 0
    passive_skill_id: str | None = None    # skill_id granted automatically on evolution
    trigger_type: SpeciesEvolutionTrigger = SpeciesEvolutionTrigger.LEVEL
    trigger_value: str = "10"              # level number, quest_id, item_id, etc.
    min_alignment: float | None = None     # None = no alignment requirement
    max_alignment: float | None = None
    lore_description: str = ""             # shown to player with high WIS


class SpeciesDefinition(BaseModel):
    species_id: str
    name: str
    description: str
    flavor_text: str = ""
    rarity: str = "COMMON"                 # COMMON | UNCOMMON | RARE (some species are rare finds)
    base_stat_bonus: Stats = Stats(STR=0, INT=0, AGI=0, LCK=0, VIT=0, WIS=0, END=0)
    base_perception_bonus: int = 0
    stat_growth_affinity: list[str] = []   # stats that get +1 extra per 10 levels
    alignment_tendency: float = 0.0        # natural pull per 10 turns (-0.5 to +0.5)
    evolution_path: list[EvolutionStage] = []
    unique_dialogue_flags: list[str] = []  # set on player when talking to specific NPCs
    homeworld_zone_id: str = "village_start"
    available_backgrounds: list[str] = []  # background_ids valid for this species
    available_class_affinities: list[str] = []  # classes this species has affinity for
    lore_hint: str = ""                    # shown with WIS >= 15


class SpeciesRegistry:
    def __init__(self) -> None:
        self._species: dict[str, SpeciesDefinition] = {}

    def load_from_file(self, path: Path) -> None:
        data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            spec = SpeciesDefinition.model_validate(entry)
            self._species[spec.species_id] = spec

    def register(self, spec: SpeciesDefinition) -> None:
        self._species[spec.species_id] = spec

    def get(self, species_id: str) -> SpeciesDefinition | None:
        return self._species.get(species_id)

    def all(self) -> list[SpeciesDefinition]:
        return list(self._species.values())

    def available_at_start(self) -> list[SpeciesDefinition]:
        """Species selectable at character creation (rarity COMMON or UNCOMMON)."""
        return [s for s in self._species.values() if s.rarity in ("COMMON", "UNCOMMON")]
