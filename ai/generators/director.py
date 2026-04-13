"""
DirectorGenerators mixin — World Director LLM analysis and target dispatch.

These methods run exclusively in the BackgroundGenerator worker thread.
They are never called on the main game loop thread.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DirectorGenerators:
    """Mixin providing Director analysis methods for BackgroundGenerator."""

    def _gen_director_analysis(self, task: dict) -> dict | None:
        """
        Run the World Director's LLM analysis in the background thread.
        Submitted by WorldDirector.tick() — never called on the main thread.
        """
        summary        = task.get("summary", {})
        fallback_flags = task.get("fallback_flags", [])
        zone_id        = task.get("zone_id", "")
        player_level   = task.get("player_level", 1)

        system_prompt = (
            "You are the WORLD DIRECTOR for Aethoria, a post-Fracture LitRPG world. "
            "Analyze the world summary and decide what content to generate next. "
            "Return ONLY valid JSON, no explanation.\n"
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
            result = self._content_gen.client.generate_json(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
            )
            if result and "targets" in result:
                targets = result.get("targets", [])[:2]
                for target in targets:
                    self._dispatch_director_target(target, zone_id, player_level, fallback_flags)
                logger.info(f"Director analysis complete: {len(targets)} target(s) queued")
                return {"type": "director_fired", "target_count": len(targets)}
        except Exception as exc:
            logger.warning(f"Director analysis failed: {exc}")

        # Fallback: queue a lore entry so the world still gets enriched
        self._submit({
            "type": "lore_entry",
            "context_flags": fallback_flags,
            "player_flags": fallback_flags,
        })
        return None

    def _dispatch_director_target(
        self, target: dict, zone_id: str, player_level: int, flags: list[str]
    ) -> None:
        """Submit a generation task based on a Director target dict."""
        ttype = target.get("type", "")
        goal  = target.get("goal", "")
        zone  = target.get("zone") or zone_id

        if ttype == "quest":
            # Director doesn't have a player reference — generate a world_event hint
            # instead of a broken quest task (player=None silently fails in _gen_quest).
            self._submit({
                "type": "world_event", "zone_id": zone,
                "zone_name": zone.replace("_", " ").title(),
                "player_level": player_level,
                "context_flags": flags + [f"quest_seed:{goal[:40]}"],
            })
        elif ttype in ("rumor", "lore"):
            key = "rumor" if ttype == "rumor" else "lore_entry"
            self._submit({
                "type": key, "zone_id": zone,
                "context_flags": flags + [goal[:40]],
                "player_level": player_level, "player_flags": flags,
            })
        elif ttype == "world_event":
            self._submit({
                "type": "world_event", "zone_id": zone,
                "zone_name": zone.replace("_", " ").title(),
                "player_level": player_level,
                "context_flags": flags + [goal[:40]],
            })
        elif ttype == "npc_branch":
            npc_id = zone or "torven_blacksmith"
            self._submit({
                "type": "npc_branch", "npc_id": npc_id,
                "npc_name": npc_id.replace("_", " ").title(),
                "player_profile": {"level": player_level, "alignment": 0, "active_class": ""},
            })
        logger.info(f"Director dispatched (bg): {ttype} — {goal[:50]}")
