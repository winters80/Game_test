"""
Economy balance — quest reward scaling.

Single source of truth for "how much gold/XP should a quest of difficulty D
give to a player of level L?". Used by:

  - quest_system.generate_ai_quest() — clamps AI-generated rewards
  - ai/prompt_builder.build_quest_generation_prompt() — tells the LLM the
    appropriate range for the player so it doesn't ask the player to risk
    their life for "250g" when the same player can't yet afford the 50g
    iron sword on the village shelf.

## Reference economy

Hand-crafted item prices set the reality check:

  - Tier 1 (low-level): rusty_sword 10g, iron_sword 50g, leather_armor 40g,
                        health_potion 25g
  - Tier 2 (uncommon):  mage_robes 150g, shadow_daggers 120g
  - Tier 3 (rare):      enchanted_blade 300g, shadow_dagger 280g
  - Player starts with 50g (one iron sword's worth)

Hand-crafted quest rewards range from 25g (low-tier village quest) to 2000g
(endgame political ascension). The reward should be "proportional to player
power and quest danger" — a level-1 errand pays the price of a potion or
two, a level-15 epic quest pays for a top-tier weapon.

## Formula

  gold = BASE_GOLD_PER_LEVEL * player_level * tier_multiplier
  xp   = BASE_XP_PER_LEVEL_REWARD * player_level * tier_multiplier

with the four tier multipliers:

  trivial   0.5×   (courier, message delivery, errand)
  standard  1.0×   (most quests — investigate, retrieve, deliver)
  hard      2.0×   (multi-stage, dangerous combat, dungeon entry)
  epic      4.0×   (faction-defining, endgame, ascension)

Both gold and XP are clamped to [MIN_QUEST_GOLD, MAX_QUEST_GOLD] /
[MIN_QUEST_XP, MAX_QUEST_XP] so absurd inputs (e.g. player_level=0 or a
hallucinating LLM picking 999_999) cannot blow up the economy.
"""
from __future__ import annotations

from typing import Literal

# ── Scale constants ──────────────────────────────────────────────────────────

BASE_GOLD_PER_LEVEL = 20            # gold pieces per player level at standard tier
BASE_XP_PER_LEVEL_REWARD = 50       # XP per player level at standard tier

MIN_QUEST_GOLD = 5                  # never reward less than this (gold pieces)
MAX_QUEST_GOLD = 5000               # absolute cap — matches MAX_QUEST_REWARD_GOLD
MIN_QUEST_XP = 25
MAX_QUEST_XP = 50_000

Difficulty = Literal["trivial", "standard", "hard", "epic"]

TIER_MULTIPLIERS: dict[str, float] = {
    "trivial":  0.5,
    "standard": 1.0,
    "hard":     2.0,
    "epic":     4.0,
}


# ── Public API ───────────────────────────────────────────────────────────────

def recommended_quest_reward(
    player_level: int,
    difficulty: Difficulty = "standard",
) -> tuple[int, int]:
    """Return the (gold, xp) tuple a quest of this difficulty should pay
    to a player of this level. Both values are clamped.
    """
    level = max(1, int(player_level))
    mult = TIER_MULTIPLIERS.get(difficulty, 1.0)

    gold = int(BASE_GOLD_PER_LEVEL * level * mult)
    xp   = int(BASE_XP_PER_LEVEL_REWARD * level * mult)

    gold = max(MIN_QUEST_GOLD, min(gold, MAX_QUEST_GOLD))
    xp   = max(MIN_QUEST_XP,   min(xp,   MAX_QUEST_XP))
    return gold, xp


def quest_reward_bounds(
    player_level: int,
    difficulty: Difficulty = "standard",
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return ((gold_min, gold_max), (xp_min, xp_max)) for the suggested
    reward range. The recommended value sits in the middle; min/max give
    LLM prompts some honest variance to choose from.
    """
    base_gold, base_xp = recommended_quest_reward(player_level, difficulty)
    gold_min = max(MIN_QUEST_GOLD, int(base_gold * 0.5))
    gold_max = min(MAX_QUEST_GOLD, int(base_gold * 1.5))
    xp_min   = max(MIN_QUEST_XP,   int(base_xp * 0.5))
    xp_max   = min(MAX_QUEST_XP,   int(base_xp * 1.5))
    return (gold_min, gold_max), (xp_min, xp_max)


def clamp_quest_reward(
    proposed_gold: int,
    proposed_xp: int,
    player_level: int,
    difficulty: Difficulty = "standard",
) -> tuple[int, int]:
    """Clamp an external reward proposal (e.g. AI output) to the bounds
    appropriate for the player's level + the quest's difficulty.

    Returns the clamped (gold, xp). Always falls within MIN/MAX globals
    and within ±1.5× the level-scaled recommendation. Use this on every
    AI-generated reward before applying it to the player.
    """
    (gold_min, gold_max), (xp_min, xp_max) = quest_reward_bounds(player_level, difficulty)
    gold = max(gold_min, min(int(proposed_gold or 0), gold_max))
    xp   = max(xp_min,   min(int(proposed_xp or 0),   xp_max))
    return gold, xp


def infer_difficulty_from_stages(stage_count: int) -> Difficulty:
    """Cheap heuristic when no explicit difficulty is provided — used by
    the AI quest path where the LLM picks the stage count organically.
    """
    if stage_count <= 1:
        return "trivial"
    if stage_count == 2:
        return "standard"
    if stage_count == 3:
        return "hard"
    return "epic"


# ── Reference price hints for AI prompt ──────────────────────────────────────

def gear_price_hint(player_level: int) -> str:
    """One-line summary of what gear in the player's tier costs, used by
    the AI prompt so the LLM knows what "a reasonable amount" looks like.
    """
    if player_level <= 3:
        return ("At this tier basic gear costs: rusty sword 10g, "
                "iron sword 50g, leather armor 40g, health potion 25g.")
    if player_level <= 7:
        return ("At this tier uncommon gear costs: mage robes 150g, "
                "shadow daggers 120g, major health potion 65g.")
    if player_level <= 12:
        return ("At this tier rare gear costs: enchanted blade 300g, "
                "shadow dagger 280g, void crystal fragment 80g.")
    return ("At this tier endgame gear and life tokens cost 400–1500g; "
            "the player can plausibly afford a small fortune.")


# ── Generated goods (AI world expansion) ─────────────────────────────────────
#
# Caps for items the AI invents at runtime, so a gathering action can't mint
# a fortune. value_gold is whole gold, like authored items.

def generated_item_value_cap(player_level: int, item_type: str) -> int:
    """Max value_gold for an AI-invented item of this type at this level."""
    level = max(1, int(player_level))
    if item_type == "CONSUMABLE":
        return 5 + 4 * level
    return 3 + 2 * level          # MATERIAL


def generated_effect_cap(player_level: int) -> int:
    """Max heal amount for an AI-invented consumable."""
    return 15 + 5 * max(1, int(player_level))
