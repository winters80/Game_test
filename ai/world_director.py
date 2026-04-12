"""
WorldDirector — Rule-based AI director that analyzes world state and submits
targeted generation tasks to the BackgroundGenerator.

The Director runs on a longer interval (every 30 turns) and decides WHAT to
generate next based on gaps, story arcs, and balance.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai.background_generator import BackgroundGenerator
    from ai.ollama_client import OllamaClient
    from core.state_manager import GameState

logger = logging.getLogger(__name__)


class WorldDirector:
    """
    Analyses world state and decides what the background generator should
    create next. Runs on a long interval to provide strategic direction.
    """

    def __init__(
        self,
        bg_generator: "BackgroundGenerator",
        ollama_client: "OllamaClient",
        lore_data: dict,
    ) -> None:
        self._bg = bg_generator
        self._client = ollama_client
        self._lore = lore_data
        self._plan: list[dict] = []   # queued director targets
        self._last_directed_turn: int = -1
        self._wall_clock_interval: float = 1200.0
        self._last_wall_clock_run: float = 0.0

    def tick(self, state: "GameState", turn: int) -> None:
        """Called each game loop. Directs on wall-clock timer (20 min) regardless of turns."""
        import time as _time
        from config import DIRECTOR_INTERVAL

        # Update the bg generator's context on every tick (cheap)
        if self._bg:
            flags = [k for k in state.player.flags if not k.startswith("_")][:10]
            zone_name = state.current_scene_id.replace("_", " ").title()
            self._bg.update_context(
                zone_id=state.current_scene_id,
                zone_name=zone_name,
                player_level=state.player.level,
                flags=flags,
            )

        # Wall-clock director analysis every 20 minutes
        now = _time.monotonic()
        if now - self._last_wall_clock_run < self._wall_clock_interval:
            return
        self._last_wall_clock_run = now

        logger.info(f"WorldDirector firing at turn {turn} (wall-clock interval)")
        self._analyze_and_direct(state)

    def _analyze_and_direct(self, state: "GameState") -> None:
        """Build a world summary, call the Director LLM, queue generation tasks."""
        summary = self._build_world_summary(state)
        plan = self._call_director(summary)
        if not plan:
            # Fallback: submit a lore entry without LLM direction
            flags = [k for k in state.player.flags if not k.startswith("_")][:6]
            self._bg.submit_lore_entry(context_flags=flags, player_flags=flags)
            return

        for target in plan.get("targets", [])[:3]:  # max 3 per cycle
            self._dispatch_target(target, state)

    def _build_world_summary(self, state: "GameState") -> dict:
        player = state.player
        flags = [k for k in player.flags if not k.startswith("_")]

        # Count known content gaps
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
            }
        }

    def _call_director(self, summary: dict) -> dict | None:
        system_prompt = (
            "You are the WORLD DIRECTOR for Aethoria, a post-Fracture LitRPG world. "
            "Analyze the world summary and decide what content to generate next. "
            "Return ONLY valid JSON, no explanation.\n"
            "JSON format:\n"
            '{"priority": "story"|"world"|"mechanics", '
            '"targets": [{"type": "quest"|"rumor"|"lore"|"world_event"|"npc_branch", '
            '"goal": "short description", "zone": "zone_id or null"}]}'
        )
        user_prompt = (
            f"World summary: {summary}\n\n"
            "Choose 1-2 targets to generate next. "
            "Prioritize what is missing or would most enrich the player's current experience. "
            "Be specific about the goal — reference actual world context."
        )
        try:
            result = self._client.generate_json(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
            )
            if result and "targets" in result:
                return result
        except Exception as e:
            logger.warning(f"Director LLM call failed: {e}")
        return None

    def _dispatch_target(self, target: dict, state: "GameState") -> None:
        ttype = target.get("type", "")
        goal  = target.get("goal", "")
        zone  = target.get("zone") or state.current_scene_id
        player = state.player
        flags = [k for k in player.flags if not k.startswith("_")][:8]

        if ttype == "quest":
            self._bg.submit_quest(
                zone_id=zone,
                npc_hint=goal[:60],
                player=player,
            )
        elif ttype == "rumor":
            self._bg.submit_rumor(
                zone_id=zone,
                context_flags=flags + [goal[:40]],
                player_level=player.level,
            )
        elif ttype == "lore":
            self._bg.submit_lore_entry(
                context_flags=flags + [goal[:40]],
                player_flags=flags,
            )
        elif ttype == "world_event":
            scene = None  # we don't have registry here, use zone as name
            self._bg.submit_world_event(
                zone_id=zone,
                zone_name=zone.replace("_", " ").title(),
                player_level=player.level,
                context_flags=flags + [goal[:40]],
            )
        elif ttype == "npc_branch":
            npc_id = zone  # zone field repurposed as npc_id hint
            self._bg.submit_npc_branch(
                npc_id=npc_id or "torven_blacksmith",
                npc_name=npc_id.replace("_", " ").title() if npc_id else "Torven",
                player_profile={
                    "level": player.level,
                    "alignment": player.alignment,
                    "active_class": player.active_class or "Unclassified",
                },
            )
        logger.info(f"Director dispatched: {ttype} — {goal[:50]}")
