"""
WorldDirector — Rule-based AI director that analyzes world state and submits
targeted generation tasks to the BackgroundGenerator.

The Director runs on a longer interval (every DIRECTOR_INTERVAL turns, or every
5 minutes wall-clock) and decides WHAT to generate next.

IMPORTANT: The Director never calls Ollama directly. It builds a lightweight
world summary on the main thread (pure Python) and submits a "director_analysis"
task to the BackgroundGenerator queue. The actual LLM call runs in the BG worker
thread, keeping the main game loop fully non-blocking.
"""
from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.background_generator import BackgroundGenerator
    from ai.ollama_client import OllamaClient
    from core.state_manager import GameState

logger = logging.getLogger(__name__)


class WorldDirector:
    """
    Analyses world state and decides what the background generator should
    create next. Runs on a long interval to provide strategic direction.
    All Ollama calls happen in the BackgroundGenerator worker thread.
    """

    def __init__(
        self,
        bg_generator: "BackgroundGenerator",
        ollama_client: "OllamaClient",
        lore_data: dict,
    ) -> None:
        self._bg = bg_generator
        self._client = ollama_client   # kept for potential future use; not called on main thread
        self._lore = lore_data
        self._plan: list[dict] = []
        self._last_directed_turn: int = -1
        self._wall_clock_interval: float = 300.0
        # Initialise to NOW so the wall-clock check doesn't fire on turn 1
        self._last_wall_clock_run: float = _time.monotonic()

    def tick(self, state: "GameState", turn: int) -> None:
        """Called each game loop. Non-blocking — submits tasks to background thread."""
        from config import DIRECTOR_INTERVAL

        now = _time.monotonic()

        # Update the bg generator's context on every tick (cheap, always runs)
        if self._bg:
            flags = [k for k in state.player.flags if not k.startswith("_")][:10]
            zone_name = state.current_scene_id.replace("_", " ").title()
            self._bg.update_context(
                zone_id=state.current_scene_id,
                zone_name=zone_name,
                player_level=state.player.level,
                flags=flags,
            )

        # Decide whether to fire this tick
        should_fire = False
        if (turn > 0
                and turn != self._last_directed_turn
                and turn % DIRECTOR_INTERVAL == 0):
            should_fire = True
        elif now - self._last_wall_clock_run >= self._wall_clock_interval:
            should_fire = True

        if not should_fire:
            return

        # Build summary on main thread (pure Python, very fast)
        summary = self._build_world_summary(state)
        flags = [k for k in state.player.flags if not k.startswith("_")][:6]

        # Submit to background thread — never block main thread
        if self._bg:
            submitted = self._bg._submit({
                "type": "director_analysis",
                "summary": summary,
                "fallback_flags": flags,
                "zone_id": state.current_scene_id,
                "player_level": state.player.level,
            })
            if submitted:
                self._last_directed_turn = turn
                self._last_wall_clock_run = now
                logger.info(f"WorldDirector submitted analysis task at turn {turn}")
            else:
                logger.debug("WorldDirector: BG queue full, skipping this cycle")

    def _build_world_summary(self, state: "GameState") -> dict:
        """Build a lightweight world summary dict. Pure Python — no I/O."""
        player = state.player
        flags = [k for k in player.flags if not k.startswith("_")]

        active_quests = len(player.active_quest_ids)
        completed_flags = [f for f in flags if "completed" in f or "done" in f or "defeated" in f]

        return {
            "player_level": player.level,
            "player_class": player.active_class or player.base_class or "Unclassified",
            "player_alignment": round(player.alignment, 1),
            "current_zone": state.current_scene_id,
            "active_quests": active_quests,
            "story_flags": flags[:12],
            "completed_objectives": len(completed_flags),
            "gaps": {
                "needs_more_quests": active_quests < 2,
                "needs_lore": "fracture_data_recorded" not in flags,
                "needs_faction_content": "faction_joined" not in flags,
            },
        }
