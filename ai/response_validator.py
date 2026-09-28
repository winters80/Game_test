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


# ── Full AI-generated skill (used by give_skill: / quest rewards / inspect) ──

class AISkillEffectResponse(BaseModel):
    """Single effect within an AI-generated skill."""
    effect_type: Literal["damage", "heal", "buff", "debuff", "shield", "drain", "summon"] = "damage"
    scaling_stat: str | None = "LCK"   # one of STR/INT/AGI/LCK/VIT/WIS/END
    base_value: float = 6.0
    scaling_coefficient: float = 1.0

    @field_validator("scaling_stat", mode="before")
    @classmethod
    def normalise_stat(cls, v):
        if not v:
            return None
        v = str(v).upper().strip()
        return v if v in {"STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"} else "LCK"

    @field_validator("base_value", mode="before")
    @classmethod
    def clamp_base(cls, v):
        # Generous floor (we want skills to do *something*) + tight ceiling
        # (Skill model itself enforces le=200 — this catches absurd inputs early).
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 6.0
        return max(0.0, min(f, 200.0))

    @field_validator("scaling_coefficient", mode="before")
    @classmethod
    def clamp_coeff(cls, v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(f, 10.0))


class AISkillResponse(BaseModel):
    """Full skill returned by ContentGenerator.generate_skill.

    Used by every contextual skill-generation path (give_skill: trigger,
    quest reward_skill_hints, inspect, future trainer NPC). The Skill model's
    own validators apply a second pass of clamping, so the values here are
    advisory — anything within these bounds will at least *load*.
    """
    skill_id: str
    name: str
    description: str
    flavor_text: str = ""
    skill_type: Literal["ACTIVE", "PASSIVE", "TRIGGERED"] = "ACTIVE"
    spell_type: str = ""
    trigger_condition: str | None = None
    mp_cost: int = 0
    cooldown_turns: int = 0
    effects: list[AISkillEffectResponse] = []
    max_level: int = 10

    @field_validator("skill_id", mode="before")
    @classmethod
    def normalise_id(cls, v):
        import re
        # Strip everything that isn't alphanumeric or underscore so we get
        # a clean snake_case slug regardless of LLM punctuation choices.
        s = re.sub(r"[^a-z0-9_]+", "_", str(v).lower().replace(" ", "_").replace("-", "_"))
        return s.strip("_")[:40] or "ai_skill"

    @field_validator("mp_cost", mode="before")
    @classmethod
    def clamp_mp(cls, v):
        try:
            i = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(i, 30))   # tight — Skill model allows 200, but in
                                    # practice no AI skill should cost > 30

    @field_validator("cooldown_turns", mode="before")
    @classmethod
    def clamp_cd(cls, v):
        try:
            i = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(i, 10))   # tight — no AI skill should be on CD > 10

    @field_validator("max_level", mode="before")
    @classmethod
    def clamp_maxlvl(cls, v):
        try:
            i = int(v)
        except (TypeError, ValueError):
            return 10
        return max(1, min(i, 20))

    @field_validator("effects")
    @classmethod
    def ensure_at_least_one_effect(cls, v):
        # Skills with no effect are decoration. Add a default DAMAGE/LCK
        # effect so the skill at least *does* something in combat.
        if not v:
            return [AISkillEffectResponse(
                effect_type="damage", scaling_stat="LCK",
                base_value=6.0, scaling_coefficient=1.0,
            )]
        return v[:3]   # cap at 3 effects per skill


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
    # Short evocative skill-name hints. Each becomes a full AI-generated
    # Skill at quest-completion time via systems.quest_system._grant_quest_skill_rewards.
    # Capped at 2 because more would dilute the reward and lengthen the
    # post-quest spinner pause.
    reward_skill_hints: list[str] = []
    alignment_reward: float = 0.0
    faction_rewards: dict[str, float] = {}   # {faction_id: standing_delta}
    guild_rewards: dict[str, float] = {}     # {guild_id: standing_delta}
    flavor_text: str = ""

    @field_validator("reward_skill_hints", mode="before")
    @classmethod
    def coerce_skill_hints(cls, v) -> list[str]:
        """Accept the half-dozen ways an LLM might phrase a list of hints."""
        if not v:
            return []
        if isinstance(v, str):
            return [v.strip()][:2]
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip()[:80])
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("hint") or item.get("skill")
                    if name:
                        out.append(str(name).strip()[:80])
            return out[:2]
        return []

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
    options: list[AIDynamicOption] = []

    @field_validator("options", mode="before")
    @classmethod
    def salvage_options(cls, v: object) -> list:
        """Small models get the nesting wrong: one option as a bare object,
        stray strings in the list, "text" instead of "label", no id. Keep
        every option that has a usable label, drop the rest."""
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            return []
        kept = []
        for opt in v:
            if not isinstance(opt, dict):
                continue
            label = next((opt[k] for k in ("label", "text", "option", "name", "action")
                          if isinstance(opt.get(k), str) and opt[k].strip()), None)
            if label is None:
                continue
            opt = {**opt, "label": label.strip()}
            if not isinstance(opt.get("option_id"), str) or not opt["option_id"].strip():
                opt["option_id"] = "_".join(label.lower().split()[:4])
            if not isinstance(opt.get("triggers"), list):
                opt["triggers"] = []
            if not isinstance(opt.get("requires"), dict):
                opt["requires"] = {}
            if not isinstance(opt.get("narrative"), str):
                opt["narrative"] = ""
            kept.append(opt)
        return kept[:4]  # max 4 dynamic options


# Structured-output schema for the fast model: Ollama constrains generation
# to this shape, so a 1B model can't flatten the option objects.
DYNAMIC_OPTIONS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "situation_text": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "option_id": {"type": "string"},
                    "label": {"type": "string"},
                    "narrative": {"type": "string"},
                    "triggers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["option_id", "label", "narrative", "triggers"],
            },
        },
    },
    "required": ["situation_text", "options"],
}


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


# ── World expansion (primary model reacting to a player action) ──────────────
#
# Loose on shape, strict on content: ids are slugged, lists capped, and
# anything numeric is re-clamped by systems/world_growth + systems/economy
# before it touches the game. Item types are limited to crafting materials
# and consumables so a model can't mint gear.

def _slug(v: object, max_len: int = 40) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", str(v or "").strip().lower()).strip("_")
    return s[:max_len].rstrip("_")


class AIWorldItem(BaseModel):
    item_id: str
    name: str
    description: str = ""
    item_type: Literal["MATERIAL", "CONSUMABLE"] = "MATERIAL"
    value_gold: int = 1
    effect_type: Literal["", "heal_hp", "heal_mp"] = ""
    effect_value: int = 0

    @field_validator("item_id", mode="before")
    @classmethod
    def _id(cls, v: object) -> str:
        return _slug(v)

    @field_validator("item_type", mode="before")
    @classmethod
    def _type(cls, v: object) -> str:
        return "CONSUMABLE" if str(v).upper() == "CONSUMABLE" else "MATERIAL"

    @field_validator("effect_type", mode="before")
    @classmethod
    def _effect(cls, v: object) -> str:
        v = str(v or "").lower()
        return v if v in ("heal_hp", "heal_mp") else ""

    @field_validator("value_gold", "effect_value", mode="before")
    @classmethod
    def _int(cls, v: object) -> int:
        try:
            return max(0, int(float(v)))
        except (TypeError, ValueError):
            return 0


class AIWorldIngredient(BaseModel):
    item_id: str
    qty: int = 1

    @field_validator("item_id", mode="before")
    @classmethod
    def _id(cls, v: object) -> str:
        return _slug(v)

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v: object) -> int:
        try:
            return max(1, min(10, int(v)))
        except (TypeError, ValueError):
            return 1


class AIWorldRecipe(BaseModel):
    recipe_id: str
    name: str = ""
    ingredients: list[AIWorldIngredient]
    output_item_id: str
    output_qty: int = 1

    @field_validator("recipe_id", "output_item_id", mode="before")
    @classmethod
    def _id(cls, v: object) -> str:
        return _slug(v)

    @field_validator("ingredients")
    @classmethod
    def _cap(cls, v: list) -> list:
        return v[:4]


class AIWorldTradeOffer(BaseModel):
    item_id: str
    price_gold: int = 1

    @field_validator("item_id", mode="before")
    @classmethod
    def _id(cls, v: object) -> str:
        return _slug(v)

    @field_validator("price_gold", mode="before")
    @classmethod
    def _price(cls, v: object) -> int:
        try:
            return max(1, int(float(v)))
        except (TypeError, ValueError):
            return 1


class AIWorldTrader(BaseModel):
    name: str
    description: str = ""
    greeting: str = ""
    location: Literal["outer_market", "verath", "camp", "road"] = "outer_market"
    buys: list[str] = []
    sells: list[AIWorldTradeOffer] = []

    @field_validator("location", mode="before")
    @classmethod
    def _loc(cls, v: object) -> str:
        v = _slug(v)
        return v if v in ("outer_market", "verath", "camp", "road") else "outer_market"

    @field_validator("buys", mode="before")
    @classmethod
    def _buys(cls, v: object) -> list[str]:
        return [str(x) for x in (v or [])][:6]

    @field_validator("sells")
    @classmethod
    def _sells(cls, v: list) -> list:
        return v[:4]


class AIWorldExpansionResponse(BaseModel):
    """What the world grows in response to a player action."""
    summary: str = ""                       # one in-world sentence ("Word spreads…")
    yield_item: AIWorldItem | None = None   # what repeating the action produces
    items: list[AIWorldItem] = []           # derived goods (e.g. smelted ingot)
    recipes: list[AIWorldRecipe] = []
    trader: AIWorldTrader | None = None
    skill_hint: str = ""                    # optional skill the action teaches

    @field_validator("items")
    @classmethod
    def _items(cls, v: list) -> list:
        return v[:3]

    @field_validator("recipes")
    @classmethod
    def _recipes(cls, v: list) -> list:
        return v[:2]

    @field_validator("skill_hint", mode="before")
    @classmethod
    def _hint(cls, v: object) -> str:
        return str(v or "").strip()[:40]
