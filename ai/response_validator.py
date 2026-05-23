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
    # GOLD PIECES (not copper) — see entities/quest.py for the unit contract.
    # Clamped to MAX_QUEST_REWARD_GOLD so a hallucinating model can't drop a
    # six-figure jackpot on the player.
    reward_gold: int = 0
    reward_xp: int = 0
    reward_items: list[str] = []
    alignment_reward: float = 0.0
    faction_rewards: dict[str, float] = {}   # {faction_id: standing_delta}
    guild_rewards: dict[str, float] = {}     # {guild_id: standing_delta}
    flavor_text: str = ""

    @field_validator("reward_gold", mode="before")
    @classmethod
    def clamp_reward_gold(cls, v):
        """Clamp to the same ceiling QuestTemplate enforces (gold pieces)."""
        from entities.quest import MAX_QUEST_REWARD_GOLD
        try:
            v_int = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(v_int, MAX_QUEST_REWARD_GOLD))

    @field_validator("reward_items", mode="before")
    @classmethod
    def coerce_reward_items(cls, v: list) -> list:
        """Model sometimes returns [{"item_id": "x", "quantity": 1}] — extract the id."""
        coerced = []
        for item in v:
            if isinstance(item, str):
                coerced.append(item)
            elif isinstance(item, dict):
                item_id = item.get("item_id") or item.get("id") or item.get("name")
                if item_id:
                    coerced.append(str(item_id))
        return coerced


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

    @field_validator("triggers", mode="before")
    @classmethod
    def cap_triggers(cls, v: list) -> list:
        coerced = []
        for t in v[:8]:
            if isinstance(t, str):
                coerced.append(t)
            elif isinstance(t, dict):
                # Model returned {"flag": "X"} or {"type":"flag","value":"X"} — reconstruct
                if "flag" in t:
                    coerced.append(f"flag:{t['flag']}")
                elif "type" in t and "value" in t:
                    coerced.append(f"{t['type']}:{t['value']}")
        return coerced


class AIDynamicOptionsResponse(BaseModel):
    """Response from the AI when a player asks about the situation."""
    situation_text: str   # narrative paragraph explaining the possibilities
    options: list[AIDynamicOption]

    @field_validator("options")
    @classmethod
    def cap_options(cls, v: list) -> list:
        return v[:4]  # max 4 dynamic options


# ── Guild AI response models ──────────────────────────────────────────────────

class AIGuildRankResponse(BaseModel):
    rank_id: str
    name: str
    standing_required: int
    title: str = ""
    description: str = ""

    @field_validator("rank_id")
    @classmethod
    def normalize_id(cls, v: str) -> str:
        return v.lower().replace(" ", "_").replace("-", "_")


class AIGuildPerkResponse(BaseModel):
    perk_id: str
    name: str
    description: str
    rank_required: str
    stat_bonuses: dict[str, int] = {}
    skill_unlocks: list[str] = []

    @field_validator("perk_id")
    @classmethod
    def normalize_id(cls, v: str) -> str:
        return v.lower().replace(" ", "_").replace("-", "_")

    @field_validator("skill_unlocks")
    @classmethod
    def cap_skills(cls, v: list) -> list:
        return v[:3]

    @field_validator("stat_bonuses")
    @classmethod
    def cap_bonus(cls, v: dict) -> dict:
        return {k: min(v[k], 5) for k in v}  # cap +5 per stat, no exploit


class AIGuildTemplateResponse(BaseModel):
    guild_id: str
    name: str
    description: str
    flavor_text: str = ""
    archetype: str
    ranks: list[AIGuildRankResponse]
    perks: list[AIGuildPerkResponse]

    @field_validator("guild_id")
    @classmethod
    def normalize_id(cls, v: str) -> str:
        return "gen_" + v.lower().replace(" ", "_").replace("-", "_")[:30]

    @field_validator("ranks")
    @classmethod
    def validate_ranks(cls, v: list) -> list:
        if len(v) < 2:
            raise ValueError("Guild needs at least 2 ranks")
        return v[:6]

    @field_validator("perks")
    @classmethod
    def cap_perks(cls, v: list) -> list:
        return v[:4]
