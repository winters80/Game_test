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
