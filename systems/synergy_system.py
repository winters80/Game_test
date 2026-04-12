"""
Combat stat synergy system.

Synergies activate when a player's stats meet all thresholds for a given pairing.
They apply bonus modifiers to damage, defense, dodge chance, or MP regen.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entities.player import Player

SYNERGIES: list[dict] = [
    {
        "name": "Berserker's Edge",
        "stats": {"STR": 10, "AGI": 8},
        "bonus": {"damage_pct": 15},
        "description": "Raw speed meets brute force.",
    },
    {
        "name": "Arcane Intellect",
        "stats": {"INT": 12, "WIS": 8},
        "bonus": {"mp_regen": 2},
        "description": "Deep knowledge amplifies mana flow.",
    },
    {
        "name": "Iron Will",
        "stats": {"END": 10, "VIT": 8},
        "bonus": {"damage_reduction": 10},
        "description": "Body and resolve become one.",
    },
    {
        "name": "Shadow Step",
        "stats": {"AGI": 12, "LCK": 8},
        "bonus": {"dodge_pct": 10},
        "description": "Fortune favours the swift.",
    },
]


def get_active_synergies(player: "Player") -> list[dict]:
    """Return synergies whose stat thresholds are all met by the player."""
    active = []
    for syn in SYNERGIES:
        if all(
            getattr(player.stats, stat, 0) >= threshold
            for stat, threshold in syn["stats"].items()
        ):
            active.append(syn)
    return active


def apply_synergy_bonuses(
    player: "Player", base_dmg: int, base_def: int
) -> tuple[int, int]:
    """Apply active synergy bonuses to base damage and defense values.

    Returns (modified_damage, modified_defense).
    Also applies mp_regen in place.
    """
    synergies = get_active_synergies(player)
    dmg = base_dmg
    defense = base_def
    for syn in synergies:
        bonus = syn["bonus"]
        if "damage_pct" in bonus:
            dmg = int(dmg * (1 + bonus["damage_pct"] / 100))
        if "damage_reduction" in bonus:
            # Increase effective defense by the reduction percentage
            defense = int(defense * (1 + bonus["damage_reduction"] / 100))
        if "mp_regen" in bonus:
            player.current_mp = min(player.max_mp, player.current_mp + bonus["mp_regen"])
    return dmg, defense
