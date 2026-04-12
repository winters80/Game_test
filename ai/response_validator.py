from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class AISkillStub(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: Literal["ACTIVE", "PASSIVE", "TRIGGERED"] = "ACTIVE"
    effect_hint: str = ""

    @field_validator("skill_id")
    @classmethod
    def normalize_skill_id(cls, v: str) -> str:
        return v.lower().replace(" ", "_").replace("-", "_")


class AIClassResponse(BaseModel):
    class_id: str
    name: str
    rarity: Literal["RARE", "EPIC", "LEGENDARY"] = "RARE"
    description: str
    flavor_text: str = ""
    skills: list[AISkillStub]
    lore_hook: str = ""

    @field_validator("class_id")
    @classmethod
    def normalize_class_id(cls, v: str) -> str:
        return v.lower().replace(" ", "_").replace("-", "_")

    @field_validator("name")
    @classmethod
    def truncate_name(cls, v: str) -> str:
        return v[:40]

    @field_validator("skills")
    @classmethod
    def validate_skill_count(cls, v: list) -> list:
        if len(v) < 1:
            raise ValueError("Must have at least 1 skill")
        return v[:5]  # cap at 5


class AIQuestStageResponse(BaseModel):
    stage_id: str
    objective_text: str
    completion_condition: dict  # e.g. {"has_flag": "x"} or {"has_item": "x"}
    next_stage_id: str | None = None


class AIQuestResponse(BaseModel):
    template_id: str
    title: str
    description: str
    stages: list[AIQuestStageResponse]
    reward_gold: int = 0
    reward_xp: int = 0
    reward_items: list[str] = []
    alignment_reward: float = 0.0
    faction_rewards: dict[str, float] = {}   # {faction_id: standing_delta}
    guild_rewards: dict[str, float] = {}     # {guild_id: standing_delta}
    flavor_text: str = ""


class AIDynamicOption(BaseModel):
    """A single AI-generated situational option."""
    option_id: str
    label: str
    narrative: str = ""   # short description of what happens if chosen
    triggers: list[str] = []   # standard trigger strings (flag:X, combat:X, etc.)
    requires: dict = {}         # same as scene option requires dict

    @field_validator("option_id")
    @classmethod
    def normalize_id(cls, v: str) -> str:
        return "ai_" + v.lower().replace(" ", "_").replace("-", "_")[:30]

    @field_validator("triggers")
    @classmethod
    def cap_triggers(cls, v: list) -> list:
        return v[:8]


class AIDynamicOptionsResponse(BaseModel):
    """Response from the AI when a player asks about the situation."""
    situation_text: str   # narrative paragraph explaining the possibilities
    options: list[AIDynamicOption]

    @field_validator("options")
    @classmethod
    def cap_options(cls, v: list) -> list:
        return v[:4]  # max 4 dynamic options
