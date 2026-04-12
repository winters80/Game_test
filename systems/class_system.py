from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import Event, bus
from systems.progression_tracker import compute_divergence_score

if TYPE_CHECKING:
    from entities.player import Player
    from entities.character_class import ClassDefinition, ClassRegistry
    from entities.item import ItemRegistry
    from entities.skill import SkillRegistry


def assign_base_class(player: "Player", class_id: str, class_registry: "ClassRegistry", skill_registry: "SkillRegistry") -> tuple[bool, str]:
    cls = class_registry.get(class_id)
    if not cls:
        return False, f"Unknown class: {class_id}"
    player.base_class = class_id
    player.active_class = class_id
    # Apply stat bonuses
    for stat in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
        bonus = getattr(cls.base_stats_bonus, stat, 0)
        if bonus:
            current = getattr(player.stats, stat)
            setattr(player.stats, stat, current + bonus)
    # Grant starting skills
    for skill_id in cls.starting_skills:
        if skill_id not in player.skills:
            player.skills.append(skill_id)
            skill = skill_registry.get(skill_id)
            bus.publish(Event("SKILL_ACQUIRED", {
                "skill_id": skill_id,
                "skill_name": skill.name if skill else skill_id,
                "rarity": skill.rarity.value if skill else "COMMON",
            }))
    bus.publish(Event("CLASS_ASSIGNED", {
        "class_id": class_id,
        "class_name": cls.name,
        "rarity": cls.rarity.value,
    }))
    return True, f"Class assigned: {cls.name}"


def resolve_combo_class(
    player: "Player",
    class_registry: "ClassRegistry",
    item_registry: "ItemRegistry",
    skill_registry: "SkillRegistry",
    ai_content_generator: object | None = None,
) -> ClassDefinition | None:
    """
    3-layer class resolution:
    1. Exact combo match (JSON lookup)
    2. Pattern match (partial requirements)
    3. AI generation if divergence score >= threshold
    """
    player_classes = {c for c in [player.base_class, player.secondary_class] if c}
    if not player_classes:
        return None

    # Layer 1: Exact match — all required classes present AND all conditions met
    for cls in class_registry.combo_classes():
        req = cls.combo_requirements
        if not req:
            continue
        if set(req.required_classes) == player_classes:
            if _check_combo_conditions(player, cls, item_registry):
                return cls

    # Layer 2: Pattern match — subset of classes matches, ignore item/flag requirements
    for cls in class_registry.combo_classes():
        req = cls.combo_requirements
        if not req:
            continue
        if set(req.required_classes).issubset(player_classes):
            return cls  # best-effort match without full requirements

    # Layer 3: AI generation
    divergence = compute_divergence_score(player, class_registry, item_registry)
    if divergence.trigger_ai and ai_content_generator is not None:
        bus.emit(Event("ANOMALY_DETECTED", {}))
        generated_class = ai_content_generator.generate_class(player, divergence, class_registry)
        if generated_class:
            class_registry.register(generated_class)
            return generated_class

    # Fallback: return the fallback_generated class
    return class_registry.get("fallback_generated")


def _check_combo_conditions(player: "Player", cls: "ClassDefinition", item_registry: "ItemRegistry") -> bool:
    req = cls.combo_requirements
    if not req:
        return True
    if player.level < req.min_level:
        return False
    if req.min_stats and not player.stats.meets(req.min_stats):
        return False
    for item_id in req.required_items:
        if not player.has_item(item_id):
            return False
    for flag in req.required_flags:
        if not player.has_flag(flag):
            return False
    return True
