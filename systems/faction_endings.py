"""
Faction-driven endgame triggers.

Extracted from ``core/game_engine.py``. Two operations:

  update_faction_standing(engine, faction_id, delta)
      Handle the ``update_faction:id:delta`` choice trigger.

  check_ending_paths(engine)
      Called once per turn. If the player qualifies for one of the two
      political ending paths (rule_the_system / destroy_the_system) and
      hasn't been notified yet, set the flag and print the cue. Fires
      exactly once per save.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import bus
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine


_NOTIFIED_FLAG = "ending_path_notified"


def update_faction_standing(engine: "GameEngine", faction_id: str, delta: float) -> None:
    """Apply a standing delta to a faction; no-op if faction_system is offline."""
    if not engine.faction_registry:
        return
    from systems import faction_system
    faction_system.update_standing(
        faction_id, delta, engine.state, engine.faction_registry,
    )
    bus.flush()


def check_ending_paths(engine: "GameEngine") -> None:
    """Notify the player exactly once if a political ending path is unlocked."""
    if engine.state.player.has_flag(_NOTIFIED_FLAG):
        return

    from systems import faction_system
    path = faction_system.check_political_path(engine.state, engine.faction_registry)
    if not path:
        return

    engine.state.player.set_flag(_NOTIFIED_FLAG)
    engine.state.player.set_flag(path)  # 'rule_the_system' or 'destroy_the_system'

    if path == "rule_the_system":
        renderer.print_system_message(
            "THE SYSTEM ACKNOWLEDGES YOUR ASCENT. A path opens before you.",
            style="system_msg",
        )
    else:
        renderer.print_system_message(
            "THE SIGNAL IS READY. The Fracture Core awaits.",
            style="system_warning",
        )
    bus.flush()
