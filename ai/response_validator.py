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
