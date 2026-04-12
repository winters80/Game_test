from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.skill import Skill, SkillRegistry


def learn_skill(player: "Player", skill_id: str, skill_registry: "SkillRegistry") -> tuple[bool, str]:
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
    """Calculate the damage/heal value for a skill given player stats."""
    if not skill.effects:
        return 0
    total = 0
    for effect in skill.effects:
        base = effect.base_value
        if effect.scaling_stat:
            stat_val = getattr(player.stats, effect.scaling_stat, 5)
            total += base + stat_val * effect.scaling_coefficient
        else:
            total += base
    return max(1, int(total))


def cast_spell(
    player: "Player",
    skill_id: str,
    skill_registry: "SkillRegistry",
) -> tuple[int, str]:
    """Cast a spell skill. Deducts MP, sets cooldown, returns (damage, message)."""
    skill = skill_registry.get(skill_id)
    if not skill or not skill.spell_type:
        return 0, "Not a spell."
    if player.current_mp < skill.mp_cost:
        return 0, f"Not enough MP. (Need {skill.mp_cost}, have {player.current_mp})"
    cd = player.skill_cooldowns.get(skill_id, 0)
    if cd > 0:
        return 0, f"{skill.name} is on cooldown ({cd} turns remaining)."
    player.current_mp -= skill.mp_cost
    damage = calculate_skill_damage(skill, player)
    if skill.cooldown_turns > 0:
        player.skill_cooldowns[skill_id] = skill.cooldown_turns
    return damage, f"You cast {skill.name} for {damage} damage!"


def tick_skill_cooldowns(player: "Player") -> None:
    """Decrement all active skill cooldowns by 1. Remove expired ones."""
    expired = [k for k, v in player.skill_cooldowns.items() if v <= 1]
    for k in expired:
        del player.skill_cooldowns[k]
    for k in list(player.skill_cooldowns):
        player.skill_cooldowns[k] -= 1


def get_spell_skills(player: "Player", skill_registry: "SkillRegistry") -> list:
    """Return all of the player's skills that are spells (have mp_cost > 0 and spell_type set)."""
    result = []
    for sid in player.skills:
        skill = skill_registry.get(sid)
        if skill and skill.mp_cost > 0 and skill.spell_type:
            result.append(skill)
    return result
