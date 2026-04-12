from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from entities.enums import Rarity, SkillType, EffectType


class SkillEffect(BaseModel):
    effect_type: EffectType
    scaling_stat: str | None = None       # e.g. "STR", "INT"
    base_value: float = 0.0
    scaling_coefficient: float = 1.0


class Skill(BaseModel):
    skill_id: str
    name: str
    rarity: Rarity = Rarity.COMMON
    description: str
    flavor_text: str = ""
    spell_type: str = ""        # "fire" | "ice" | "arcane" | "" for non-spells
    skill_type: SkillType = SkillType.ACTIVE
    trigger_condition: str | None = None   # e.g. "on_kill", "on_low_hp"
    mp_cost: int = 0
    cooldown_turns: int = 0
    effects: list[SkillEffect] = []
    level: int = 1
    max_level: int = 10
    level_up_bonus: dict[str, float] = {}
    is_ai_generated: bool = False


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_from_dir(self, skills_dir: Path) -> None:
        for path in skills_dir.glob("*.json"):
            data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                skill = Skill.model_validate(entry)
                self._skills[skill.skill_id] = skill

    def register(self, skill: Skill) -> None:
        self._skills[skill.skill_id] = skill

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all(self) -> list[Skill]:
        return list(self._skills.values())
