from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from entities.enums import Rarity
from entities.player import Stats


class ComboRequirements(BaseModel):
    required_classes: list[str]          # e.g. ["warrior", "mage"]
    min_stats: Stats | None = None       # e.g. Stats(LCK=25)
    required_items: list[str] = []       # Item IDs that must be in inventory
    required_flags: list[str] = []       # Story flags that must be set
    min_level: int = 1


class ClassDefinition(BaseModel):
    class_id: str
    name: str
    rarity: Rarity = Rarity.COMMON
    description: str
    flavor_text: str = ""
    base_stats_bonus: Stats = Stats(STR=0, INT=0, AGI=0, LCK=0, VIT=0, WIS=0, END=0)
    stat_growth: Stats = Stats(STR=0, INT=0, AGI=0, LCK=0, VIT=0, WIS=0, END=0)
    starting_skills: list[str] = []
    learnable_skills: list[str] = []
    combo_requirements: ComboRequirements | None = None
    is_ai_generated: bool = False
    generation_context: dict[str, Any] | None = None


class ClassRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, ClassDefinition] = {}

    def load_from_file(self, path: Path) -> None:
        data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            cls = ClassDefinition.model_validate(entry)
            self._classes[cls.class_id] = cls

    def register(self, cls: ClassDefinition) -> None:
        self._classes[cls.class_id] = cls

    def get(self, class_id: str) -> ClassDefinition | None:
        return self._classes.get(class_id)

    def all(self) -> list[ClassDefinition]:
        return list(self._classes.values())

    def combo_classes(self) -> list[ClassDefinition]:
        return [c for c in self._classes.values() if c.combo_requirements is not None]

    def base_classes(self) -> list[ClassDefinition]:
        return [c for c in self._classes.values() if c.combo_requirements is None and not c.is_ai_generated]
