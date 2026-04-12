"""
Species System

Handles:
  - Applying species stat bonuses at character creation
  - Checking and triggering species evolution on level-up
  - Background application (stat bonuses + starting items + flags)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.event_bus import Event, bus
from entities.enums import SpeciesEvolutionTrigger

if TYPE_CHECKING:
    from entities.player import Player
    from entities.species import SpeciesDefinition, SpeciesRegistry, EvolutionStage


def apply_species(player: "Player", species: "SpeciesDefinition") -> None:
    """
    Called once at character creation when a species is chosen.
    Applies base stat bonuses and sets species flags.
    """
    player.species_id = species.species_id
    player.perception_bonus += species.base_perception_bonus

    for stat in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
        bonus = getattr(species.base_stat_bonus, stat, 0)
        if bonus:
            current = getattr(player.stats, stat)
            setattr(player.stats, stat, current + bonus)

    for flag in species.unique_dialogue_flags:
        player.set_flag(flag)

    # Recalculate HP/MP with new stats
    player.max_hp = 20 + player.stats.VIT * 5 + player.stats.END * 3
    player.current_hp = player.max_hp
    player.max_mp = 10 + player.stats.INT * 3 + player.stats.WIS * 2
    player.current_mp = player.max_mp

    bus.publish(Event("SPECIES_SELECTED", {
        "species_id": species.species_id,
        "species_name": species.name,
    }))


def apply_background(
    player: "Player",
    background_data: dict[str, Any],
    item_registry: object,
) -> None:
    """Apply a background's stat bonuses, items, gold, flags, and starting alignment."""
    for stat in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
        bonus = background_data.get("stat_bonus", {}).get(stat, 0)
        if bonus:
            current = getattr(player.stats, stat)
            setattr(player.stats, stat, current + bonus)

    player.gold += background_data.get("starting_gold_bonus", 0)

    for item_id in background_data.get("starting_items", []):
        player.add_item(item_id)

    for flag in background_data.get("starting_flags", []):
        player.set_flag(flag)

    player.background_id = background_data.get("background_id")
    player.background_narrative = background_data.get("narrative", "")

    # Set starting alignment from background
    alignment_start = float(background_data.get("alignment_start", 0.0))
    player.alignment = max(-100.0, min(100.0, player.alignment + alignment_start))

    # Recalculate HP/MP after stat bonuses
    player.max_hp = 20 + player.stats.VIT * 5 + player.stats.END * 3
    player.current_hp = player.max_hp
    player.max_mp = 10 + player.stats.INT * 3 + player.stats.WIS * 2
    player.current_mp = player.max_mp


def check_evolution(
    player: "Player",
    species: "SpeciesDefinition",
    skill_registry: object,
) -> "EvolutionStage | None":
    """
    Check if the player qualifies for the next evolution stage.
    Returns the EvolutionStage if triggered, None otherwise.
    Called on every level-up.
    """
    current_stage = player.evolution_stage
    next_index = current_stage  # evolution_path is 0-indexed for stages 1,2,3

    if next_index >= len(species.evolution_path):
        return None  # already at max evolution

    next_stage = species.evolution_path[next_index]

    if not _meets_evolution_requirements(player, next_stage):
        return None

    return next_stage


def trigger_evolution(
    player: "Player",
    stage: "EvolutionStage",
    species: "SpeciesDefinition",
    skill_registry: object,
) -> None:
    """Apply an evolution stage to the player."""
    player.evolution_stage = stage.stage

    # Apply stat bonuses
    for stat in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
        bonus = getattr(stage.stat_bonus, stat, 0)
        if bonus:
            current = getattr(player.stats, stat)
            setattr(player.stats, stat, current + bonus)

    # Apply perception bonus
    player.perception_bonus += stage.perception_bonus

    # Grant passive skill
    if stage.passive_skill_id and stage.passive_skill_id not in player.skills:
        player.skills.append(stage.passive_skill_id)
        skill = skill_registry.get(stage.passive_skill_id) if skill_registry else None
        bus.publish(Event("SKILL_ACQUIRED", {
            "skill_id": stage.passive_skill_id,
            "skill_name": skill.name if skill else stage.passive_skill_id,
            "rarity": skill.rarity.value if skill else "RARE",
        }))

    # Recalculate derived stats
    player.max_hp = 20 + player.stats.VIT * 5 + player.stats.END * 3
    player.current_hp = min(player.current_hp + 20, player.max_hp)
    player.max_mp = 10 + player.stats.INT * 3 + player.stats.WIS * 2
    player.current_mp = min(player.current_mp + 10, player.max_mp)

    bus.publish(Event("SPECIES_EVOLVED", {
        "species_id": species.species_id,
        "stage": stage.stage,
        "new_name": stage.name,
        "description": stage.description,
        "flavor_text": stage.flavor_text,
    }))


def _meets_evolution_requirements(player: "Player", stage: "EvolutionStage") -> bool:
    """Check if a player meets the requirements for an evolution stage."""
    trigger = stage.trigger_type
    value = stage.trigger_value

    if trigger == SpeciesEvolutionTrigger.LEVEL:
        if player.level < int(value):
            return False

    elif trigger == SpeciesEvolutionTrigger.STAT:
        # Format: "STAT_NAME:threshold" e.g. "INT:20"
        if ":" in value:
            stat_name, threshold_str = value.split(":", 1)
            stat_val = getattr(player.stats, stat_name.upper(), 0)
            if stat_val < int(threshold_str):
                return False

    elif trigger == SpeciesEvolutionTrigger.QUEST:
        # Quest must be in completed_quest_ids or a flag must be set
        if value not in player.completed_quest_ids and not player.has_flag(f"quest_done:{value}"):
            return False

    elif trigger == SpeciesEvolutionTrigger.ALIGNMENT:
        # Value format: "min:max" or just "min" (positive = good, negative = evil)
        parts = value.split(":")
        if len(parts) >= 1 and parts[0]:
            if player.alignment < float(parts[0]):
                return False
        if len(parts) >= 2 and parts[1]:
            if player.alignment > float(parts[1]):
                return False

    elif trigger == SpeciesEvolutionTrigger.ITEM:
        if not player.has_item(value):
            return False

    # Check alignment constraints on the stage itself
    if stage.min_alignment is not None and player.alignment < stage.min_alignment:
        return False
    if stage.max_alignment is not None and player.alignment > stage.max_alignment:
        return False

    return True


def load_backgrounds(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all background definitions from data/backgrounds/backgrounds.json."""
    path = data_dir / "backgrounds" / "backgrounds.json"
    if not path.exists():
        return {}
    entries: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return {b["background_id"]: b for b in entries}
