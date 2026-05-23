"""
Camp / rest mechanics.

Extracted from ``core/game_engine.py._handle_rest``. Two rest variants:

  full   — 8 hours, restores 100% HP/MP, 8 buff-ticks
  short  — 2 hours, restores 40% HP/MP, 2 buff-ticks

Both carry a 10% ambush chance triggering a goblin_scout combat encounter.

Stays as a single function for now since rest is the only operation; if
inn-rest or wilderness-camp need to diverge, split into ``camp_rest()``
and ``inn_rest()`` rather than letting this function grow branches.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from systems.buff_system import tick_buffs
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine


_AMBUSH_CHANCE = 0.10
_AMBUSH_ENCOUNTER = "goblin_scout"


def handle_rest(engine: "GameEngine", rest_type: str = "short") -> None:
    """Restore HP/MP via camp rest, tick buffs, then roll for ambush."""
    player = engine.state.player

    if rest_type == "full":
        hp_pct, mp_pct, tick_count, label = 1.0, 1.0, 8, "Full Rest (8 hours)"
    else:
        hp_pct, mp_pct, tick_count, label = 0.4, 0.4, 2, "Light Rest (2 hours)"

    healed_hp = int(player.max_hp * hp_pct)
    healed_mp = int(player.max_mp * mp_pct)
    player.current_hp = min(player.max_hp, player.current_hp + healed_hp)
    player.current_mp = min(player.max_mp, player.current_mp + healed_mp)

    for _ in range(tick_count):
        tick_buffs(player)

    renderer.print_divider()
    renderer.print_system_message(
        f"{label}: +{healed_hp} HP, +{healed_mp} MP restored.",
        style="success",
    )

    if random.random() < _AMBUSH_CHANCE:
        renderer.print_system_message(
            "Something stirs in the dark. You are not alone.", style="system_warning",
        )
        renderer.prompt_any_key()
        engine._run_combat(_AMBUSH_ENCOUNTER)
    else:
        renderer.prompt_any_key()
