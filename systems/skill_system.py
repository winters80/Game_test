from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.skill import Skill, SkillRegistry
    from entities.character_class import ClassRegistry

logger = logging.getLogger(__name__)

# Use-count threshold for a skill to level up. Every USES_PER_LEVEL casts in
# combat the skill grows by +1 level (capped at Skill.max_level).
USES_PER_LEVEL = 5

# How much each skill level boosts the skill's effective output.
# damage = base + (lvl-1) * LEVEL_BONUS_FLAT + stat × coeff × (1 + (lvl-1)*LEVEL_BONUS_COEFF)
LEVEL_BONUS_FLAT  = 1.0      # +1 base damage per level
LEVEL_BONUS_COEFF = 0.10     # +10% stat scaling per level


def learn_skill(player: "Player", skill_id: str, skill_registry: "SkillRegistry") -> tuple[bool, str]:
    """Grant a skill to the player. Returns (success, message)."""
    skill = skill_registry.get(skill_id)
    if not skill:
        return False, "Unknown skill."
    if skill_id in player.skills:
        return False, f"You already know {skill.name}."
    player.skills.append(skill_id)
    bus.publish(Event("SKILL_ACQUIRED", {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "rarity": skill.rarity.value,
    }))
    return True, f"Learned {skill.name}!"


def get_available_skills(player: "Player", class_registry: object, skill_registry: "SkillRegistry") -> list["Skill"]:
    """Return skills available to the player's current class that they haven't learned yet."""
    active_class = class_registry.get(player.active_class or player.base_class or "")
    if not active_class:
        return []
    learnable = set(active_class.learnable_skills) - set(player.skills)
    skills = [skill_registry.get(sid) for sid in learnable]
    return [s for s in skills if s is not None]


def calculate_skill_damage(skill: "Skill", player: "Player") -> int:
    """Calculate the damage / heal value for a skill given player stats.

    Reads ``player.skill_levels[skill.skill_id]`` so skills that the player
    has used a lot hit harder. Each level above 1 adds ``LEVEL_BONUS_FLAT``
    to the base value and increases stat scaling by ``LEVEL_BONUS_COEFF``.
    Level defaults to 1 when the dict doesn't have an entry.
    """
    if not skill.effects:
        return 0

    level = player.skill_levels.get(skill.skill_id, 1)
    level_steps = max(0, level - 1)

    total = 0.0
    for effect in skill.effects:
        base = effect.base_value + level_steps * LEVEL_BONUS_FLAT
        if effect.scaling_stat:
            stat_val = getattr(player.stats, effect.scaling_stat, 5)
            coeff = effect.scaling_coefficient * (1.0 + level_steps * LEVEL_BONUS_COEFF)
            total += base + stat_val * coeff
        else:
            total += base
    return max(1, int(total))


def record_skill_use(
    player: "Player", skill_id: str, skill_registry: "SkillRegistry",
) -> int | None:
    """Increment a skill's use count and level it up if the threshold is hit.

    Returns the new level if a level-up happened, otherwise None. Caller
    decides whether to surface the level-up to the player (combat does).
    """
    skill = skill_registry.get(skill_id)
    if skill is None:
        return None

    uses = player.skill_uses.get(skill_id, 0) + 1
    player.skill_uses[skill_id] = uses

    cur_level = player.skill_levels.get(skill_id, 1)
    if cur_level >= skill.max_level:
        return None

    # Level up every USES_PER_LEVEL casts.
    if uses % USES_PER_LEVEL == 0:
        new_level = cur_level + 1
        player.skill_levels[skill_id] = new_level
        bus.publish(Event("SKILL_LEVELED_UP", {
            "skill_id": skill_id,
            "skill_name": skill.name,
            "new_level": new_level,
            "max_level": skill.max_level,
        }))
        return new_level
    return None


def grant_next_learnable_skill(
    player: "Player",
    class_registry: "ClassRegistry",
    skill_registry: "SkillRegistry",
) -> str | None:
    """Auto-grant the next unlearned skill from the player's active class.

    Called on level-up to give players incremental access to their class's
    ``learnable_skills`` list. Returns the granted skill_id, or None if the
    player has no class or has already learned every listed skill.

    Ids missing from the registry are logged and skipped, so one bad entry
    in a class's list can't block every skill listed after it.
    """
    active_id = player.active_class or player.base_class
    if not active_id:
        return None
    cls = class_registry.get(active_id)
    if not cls:
        return None
    for skill_id in cls.learnable_skills:
        if skill_id in player.skills:
            continue
        if skill_registry.get(skill_id) is None:
            # INFO, not WARNING: AI-generated classes can list unregistered
            # ids, and the console shows WARNING+ on every level-up.
            # Hand-authored data is checked by test_characters.validate_classes.
            logger.info(
                "Class '%s' lists learnable skill '%s' but it is not in the "
                "skill registry; skipping.", active_id, skill_id,
            )
            continue
        ok_, _msg = learn_skill(player, skill_id, skill_registry)
        return skill_id if ok_ else None
    return None


def reset_cooldowns_for_combat(player: "Player") -> None:
    """Clear every skill cooldown — called at the start of each combat
    encounter so cooldowns are per-fight, not per-game-loop turn."""
    player.skill_cooldowns.clear()


def tick_combat_cooldowns(player: "Player") -> None:
    """Decrement every cooldown by 1 and drop expired entries. Called at the
    end of each combat round (not each game-loop turn — that was an old
    behaviour where 5-CD skills could be refreshed by walking 5 menu steps
    in town).
    """
    expired = [k for k, v in player.skill_cooldowns.items() if v <= 1]
    for k in expired:
        del player.skill_cooldowns[k]
    for k in list(player.skill_cooldowns):
        player.skill_cooldowns[k] -= 1


# ── Deprecated (removed in skill audit) ──────────────────────────────────────
# `cast_spell` and `get_spell_skills` used to gate on the `spell_type` field,
# but zero of the 48 hand-crafted skills set it, so the functions were dead
# code. Combat uses `player_skill_attack` from combat_system directly.
# Removed in 2026-05 skill audit. If you need a "spell" classifier, key off
# `mp_cost > 0` or a fresh `is_spell` boolean — don't resurrect spell_type.
