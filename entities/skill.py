from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from entities.enums import Rarity, SkillType, EffectType

logger = logging.getLogger(__name__)

# Caps on skill numeric fields. The hard limits are wide enough that no
# legitimate content trips them but tight enough that an LLM hallucinating
# `scaling_coefficient: 999` can't one-shot a boss.
MAX_SCALING_COEFFICIENT = 10.0
MAX_BASE_VALUE = 200.0
MAX_MP_COST = 200
MAX_COOLDOWN_TURNS = 20


class SkillEffect(BaseModel):
    effect_type: EffectType
    scaling_stat: str | None = None       # e.g. "STR", "INT"
    base_value: float = Field(default=0.0, ge=0.0, le=MAX_BASE_VALUE)
    scaling_coefficient: float = Field(default=1.0, ge=0.0, le=MAX_SCALING_COEFFICIENT)


class Skill(BaseModel):
    skill_id: str
    name: str
    rarity: Rarity = Rarity.COMMON
    description: str
    flavor_text: str = ""
    spell_type: str = ""        # "fire" | "ice" | "arcane" | "" for non-spells
    skill_type: SkillType = SkillType.ACTIVE
    trigger_condition: str | None = None   # e.g. "on_kill", "on_low_hp"
    # mp_cost and cooldown_turns are clamped non-negative — a negative value
    # would make casts restore MP / never gate the skill, an obvious exploit
    # for any LLM-generated or auto-created skill.
    mp_cost: int = Field(default=0, ge=0, le=MAX_MP_COST)
    cooldown_turns: int = Field(default=0, ge=0, le=MAX_COOLDOWN_TURNS)
    effects: list[SkillEffect] = []
    level: int = 1
    max_level: int = 10
    level_up_bonus: dict[str, float] = {}
    is_ai_generated: bool = False

    @field_validator("mp_cost", "cooldown_turns", mode="before")
    @classmethod
    def _clamp_non_negative(cls, v):
        """Defensive normalisation for AI / migrated data that sneaks in negatives."""
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, iv)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_from_dir(self, skills_dir: Path) -> None:
        """Load every *.json under ``skills_dir``. Logs a clear warning when
        a later file's skill_id silently overwrites an earlier one — content
        authors should rename instead of relying on the load-order shadow.
        """
        for path in sorted(skills_dir.glob("*.json")):
            data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                skill = Skill.model_validate(entry)
                if skill.skill_id in self._skills:
                    existing = self._skills[skill.skill_id]
                    logger.warning(
                        "Skill id '%s' from %s overrides the version already loaded "
                        "(name='%s', mp=%d, cd=%d). Rename one or the other to silence this.",
                        skill.skill_id, path.name,
                        existing.name, existing.mp_cost, existing.cooldown_turns,
                    )
                self._skills[skill.skill_id] = skill

    def register(self, skill: Skill) -> None:
        self._skills[skill.skill_id] = skill

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all(self) -> list[Skill]:
        return list(self._skills.values())
