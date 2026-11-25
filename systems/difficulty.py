"""
Hard mode: an opt-in, per-save difficulty for Gates and boss lairs.

Normal is tuned so a careless build still clears every Gate at its rated
level; a focused build then keeps 55-75% of its HP. Hard asks more of it:
every enemy in a Gate or boss lair gets HARD_ENEMY_HP more HP and
HARD_ENEMY_ATK more attack, and those fights pay HARD_GOLD_BONUS more gold
(no extra XP, so Hard doesn't push the player ahead of the Gates).
Switched at the Gate Registry (core/gate_flow.open_gate_registry).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config import HARD_ENEMY_ATK, HARD_ENEMY_HP, HARD_GOLD_BONUS

if TYPE_CHECKING:
    from entities.player import Player

HARD_FLAG = "hard_mode"


def is_hard(player: "Player | None") -> bool:
    return bool(player is not None and player.flags.get(HARD_FLAG))


def set_hard(player: "Player", on: bool) -> None:
    if on:
        player.set_flag(HARD_FLAG, True)
    else:
        player.flags.pop(HARD_FLAG, None)


def applies_to(encounter_id: str) -> bool:
    """Gate rooms (``gate:<id>:<room>``) and boss lairs (``lair:<id>``), Gate masters included."""
    return (encounter_id or "").split(":", 1)[0] in ("gate", "lair")


def harden(enemies: list) -> None:
    """Give each enemy Hard mode's extra HP and attack, in place."""
    for e in enemies:
        if e is None:
            continue
        e.max_hp = max(1, round(e.max_hp * HARD_ENEMY_HP))
        e.current_hp = max(1, round(e.current_hp * HARD_ENEMY_HP))
        e.attack = max(1, round(e.attack * HARD_ENEMY_ATK))


def hard_gold(gold: int) -> int:
    return round(gold * (1 + HARD_GOLD_BONUS))
