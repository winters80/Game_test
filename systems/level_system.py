from __future__ import annotations

import math
from typing import TYPE_CHECKING

from config import (
    BASE_XP_PER_LEVEL, CLASS_MILESTONE_EVERY, STAT_POINTS_PER_LEVEL, XP_LATE_SCALING, XP_SCALING_FACTOR,
    XP_SOFT_CAP_LEVEL,
)
from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.character_class import ClassRegistry
    from entities.skill import SkillRegistry


def xp_for_level(level: int) -> int:
    """XP from ``level`` to the next: x1.5 per level up to the soft cap,
    then x``XP_LATE_SCALING``. Unchanged below the cap (old saves keep pace)."""
    early = min(level, XP_SOFT_CAP_LEVEL) - 1
    late = max(0, level - XP_SOFT_CAP_LEVEL)
    return int(BASE_XP_PER_LEVEL * (XP_SCALING_FACTOR ** early) * (XP_LATE_SCALING ** late))


def add_experience(
    player: "Player",
    amount: int,
    class_registry: "ClassRegistry | None" = None,
    skill_registry: "SkillRegistry | None" = None,
) -> list[int]:
    """Add XP and handle level-ups. Returns list of new levels reached.

    If both ``class_registry`` and ``skill_registry`` are provided, each
    level-up also auto-grants the next unlearned skill from the player's
    active class ``learnable_skills`` list (F9 — making the previously-dead
    learnable_skills data drive actual progression).
    """
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

        # Class milestone: every CLASS_MILESTONE_EVERY levels the class's
        # stat_growth is added (a warrior hardens, a mage sharpens).
        if class_registry is not None and player.level % CLASS_MILESTONE_EVERY == 0:
            apply_class_milestone(player, class_registry)

        # Auto-grant a class skill on level-up. Optional — only fires when
        # both registries are passed (combat / quest reward paths supply
        # them; legacy callers without them stay backward-compatible).
        if class_registry is not None and skill_registry is not None:
            from systems.skill_system import grant_next_learnable_skill
            grant_next_learnable_skill(player, class_registry, skill_registry)

    return leveled_up_to


def apply_class_milestone(player: "Player", class_registry: "ClassRegistry") -> dict[str, int]:
    """Add the active (or base) class's ``stat_growth`` to the player's stats.
    Returns what was added ({} if the class is unknown or has no growth)."""
    cls = class_registry.get(player.active_class or player.base_class or "")
    if cls is None:
        return {}
    gained = {s: v for s, v in cls.stat_growth.model_dump().items() if v}
    for stat, v in gained.items():
        setattr(player.stats, stat, getattr(player.stats, stat) + v)
    if gained:
        hp = 2 * (gained.get("VIT", 0) + gained.get("END", 0))
        mp = 2 * (gained.get("INT", 0) + gained.get("WIS", 0))
        player.max_hp += hp
        player.current_hp += hp
        player.max_mp += mp
        player.current_mp += mp
        bus.publish(Event("CLASS_MILESTONE", {"class_name": cls.name, "level": player.level,
                                              "gained": gained}))
    return gained


def spend_stat_point(player: "Player", stat: str) -> bool:
    """Spend one stat point on a given stat. Returns True on success."""
    valid_stats = {"STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"}
    if stat not in valid_stats or player.stat_points <= 0:
        return False
    current = getattr(player.stats, stat)
    setattr(player.stats, stat, current + 1)
    player.stat_points -= 1
    # Recalculate derived stats so HP/MP reflect the new values immediately
    recalculate_derived_stats(player)
    return True


def recalculate_derived_stats(player: "Player") -> None:
    """Recompute max_hp and max_mp from current stats. Adjusts current values proportionally."""
    new_max_hp = 20 + player.stats.VIT * 5 + player.stats.END * 3 + (player.level - 1) * (5 + player.stats.VIT + player.stats.END)
    new_max_mp = 10 + player.stats.INT * 3 + player.stats.WIS * 2 + (player.level - 1) * (2 + player.stats.INT + player.stats.WIS)
    hp_ratio = player.current_hp / player.max_hp if player.max_hp > 0 else 1.0
    mp_ratio = player.current_mp / player.max_mp if player.max_mp > 0 else 1.0
    player.max_hp = new_max_hp
    player.max_mp = new_max_mp
    player.current_hp = max(1, min(player.current_hp, int(hp_ratio * new_max_hp)))
    player.current_mp = max(0, min(player.current_mp, int(mp_ratio * new_max_mp)))


def auto_allocate_stat_points(player: "Player") -> None:
    """
    Auto-allocate all pending stat points based on the player's dominant stat.
    Used when no interactive prompt is available (headless/agent play).
    """
    if player.stat_points <= 0:
        return
    dominant = player.stats.dominant_stat()
    while player.stat_points > 0:
        spend_stat_point(player, dominant)
