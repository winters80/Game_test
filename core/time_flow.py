"""
Passing time (systems/world_clock.py).

``pass_time(engine, turns)`` advances the clock by ``turns`` extra turns and
runs the world's per-turn ticks for each one quietly: bots act, lairs restock,
buffs wear off, quests check their conditions. Used by activities that take
longer than a menu choice: Gate rooms, rests, travel between towns.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import feature

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


def pass_time(engine: "GameEngine", turns: int) -> dict:
    """Advance ``turns`` turns. Returns {"turns", "bot_events", "rumours"}."""
    from core.bot_flow import tick_bots
    from core.lair_flow import tick_lairs
    from systems.buff_system import tick_buffs

    state = engine.state
    out = {"turns": 0, "bot_events": 0, "rumours": []}
    if state is None or turns <= 0:
        return out
    for _ in range(int(turns)):
        state.advance_turn()
        out["turns"] += 1
        try:
            out["bot_events"] += len(tick_bots(engine, quiet=True))
            out["rumours"] += tick_lairs(engine, quiet=True)
        except Exception:
            logger.warning("World tick failed while passing time", exc_info=True)
        if state.player.active_buffs:
            tick_buffs(state.player)
        if feature("law_system"):
            from systems.law_system import cool_down
            cool_down(state.player, 1)
        if feature("quest_system") and engine.quest_registry:
            from systems import quest_system
            quest_system.tick_quests(state, engine.quest_registry)
    state.mark_dirty()
    return out
