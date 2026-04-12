from __future__ import annotations

import math
from typing import TYPE_CHECKING

from config import BASE_XP_PER_LEVEL, XP_SCALING_FACTOR, STAT_POINTS_PER_LEVEL
from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player


def xp_for_level(level: int) -> int:
    return int(BASE_XP_PER_LEVEL * (XP_SCALING_FACTOR ** (level - 1)))


def add_experience(player: "Player", amount: int) -> list[int]:
    """Add XP and handle level-ups. Returns list of new levels reached."""
    leveled_up_to = []
    player.experience += amount
    while player.experience >= player.experience_to_next:
        player.experience -= player.experience_to_next
        player.level += 1
        player.experience_to_next = xp_for_level(player.level)
        player.stat_points += STAT_POINTS_PER_LEVEL

        # Recalculate HP/MP based on new stats from class growth
        player.max_hp += 5 + player.stats.VIT + player.stats.END
        player.current_hp = min(player.current_hp + 10, player.max_hp)
        player.max_mp += 2 + player.stats.INT + player.stats.WIS
        player.current_mp = min(player.current_mp + 5, player.max_mp)

        leveled_up_to.append(player.level)
        bus.publish(Event("LEVEL_UP", {"level": player.level, "stat_points": STAT_POINTS_PER_LEVEL}))

    return leveled_up_to


def spend_stat_point(player: "Player", stat: str) -> bool:
    """Spend one stat point on a given stat. Returns True on success."""
    valid_stats = {"STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"}
    if stat not in valid_stats or player.stat_points <= 0:
        return False
    current = getattr(player.stats, stat)
    setattr(player.stats, stat, current + 1)
    player.stat_points -= 1
    return True
