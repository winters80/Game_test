"""
The main per-turn game loop.

Extracted from ``core/game_engine.py``. ``run_game_loop(engine)`` is the heart
of the playable game — render the current scene, prompt for a choice, apply
it, then run every per-turn tick (buffs, cooldowns, alignment inertia, quest
checks, auction expiry, faction drift, BG result drain). The finally block
guarantees the world DB closes cleanly and any BG / AIService thread pools
shut down on exit, crash, or KeyboardInterrupt.

Each per-turn tick is a one-line call into its system module — adding a new
tick means writing the system function and a one-line invocation here.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import feature
from core.event_bus import bus
from persistence.save_manager import close_game
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


def run_game_loop(engine: "GameEngine") -> None:
    """Run the main play loop until the player quits, dies, or hits an error."""
    from utils.logging_setup import log_player_error

    engine._running = True
    try:
        while engine._running and engine.state:
            bus.flush()
            _per_turn_ticks(engine)

            engine._render_scene()
            options = engine._get_current_options()
            if not options:
                renderer.print_error("No options available. Returning to main menu.")
                break

            choice = engine._prompt_choice(options)
            if choice is None:
                continue

            try:
                engine._handle_choice(choice)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log_player_error(
                    "choice_crash",
                    exc=exc,
                    player=engine.state.player,
                    scene_id=engine.state.current_scene_id,
                    node_id=engine.state.current_node_id,
                    option_id=choice.option_id,
                    turn=engine.state.player.turn_count,
                )
                logger.error("Unhandled error in _handle_choice: %s", exc, exc_info=True)
                renderer.print_error("Something went wrong. The System logged the anomaly.")
    finally:
        # Always tear everything down cleanly — exit, crash, or KeyboardInterrupt.
        if engine.state:
            close_game(engine.state)
        if engine._bg_generator:
            engine._bg_generator.stop()
        if engine.ai_service is not None:
            engine.ai_service.shutdown()


def _per_turn_ticks(engine: "GameEngine") -> None:
    """Run all the bookkeeping that happens once per loop iteration before
    rendering the scene: BG result drain, world director, buffs, cooldowns,
    alignment inertia, quest progression, auctions, faction drift.
    """
    engine._integrate_background_content()
    engine._maybe_submit_background_task()
    if engine._world_director and engine.state:
        engine._world_director.tick(engine.state, engine.state.player.turn_count)
    engine.state.advance_turn()

    # Bot adventurers live their lives (rules-based; no AI calls).
    from core.bot_flow import tick_bots
    tick_bots(engine)

    # Buff tick — decrement durations
    if engine.state.player.active_buffs:
        from systems.buff_system import tick_buffs
        tick_buffs(engine.state.player)

    # NOTE: skill cooldowns are now ticked PER COMBAT TURN (see core/combat_handler.py)
    # and reset at the start of each combat. Previously this tick ran every
    # game-loop turn, which let players "refresh" a 5-CD spell by walking
    # 5 menu steps in the village. That was F5 in the skill audit.

    # Alignment inertia — nudge toward 0 every N turns
    if feature("alignment_system"):
        from systems.alignment_system import should_apply_inertia, apply_inertia
        if should_apply_inertia(engine.state.player.turn_count):
            apply_inertia(engine.state.player)

    # Quest tick — silent completions, failures, time limits
    if feature("quest_system") and engine.quest_registry:
        from systems import quest_system
        quest_system.tick_quests(engine.state, engine.quest_registry)
        bus.flush()

    # Auction tick — expire listings, NPC counter-bids
    if feature("auction_house"):
        from systems import auction_system
        auction_system.tick_auction(engine.state)

    # Ending-path unlock (political ascension)
    if feature("faction_system") and engine.faction_registry:
        engine._check_ending_paths()

    # Autonomous faction-relation drift + matching rumour world-events
    if feature("faction_system") and feature("world_db") and engine.state.world_db:
        from systems.faction_system import drift_faction_relations
        drift_changes = drift_faction_relations(
            engine.state.world_db, engine.state.player.turn_count,
        )
        for fa, fb, delta in drift_changes:
            direction = "warmer" if delta > 0 else "cooler"
            engine.state.world_db.store_world_event(
                event_type="rumor",
                event_text=(
                    f"Relations between {fa.replace('_', ' ').title()} and "
                    f"{fb.replace('_', ' ').title()} grow {direction}."
                ),
                zone_id="verath_city",
                title="Political Shift",
                npc_hint="",
                generated_turn=engine.state.player.turn_count,
            )
