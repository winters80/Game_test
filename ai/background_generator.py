"""
BackgroundGenerator — Daemon thread that autonomously generates game content
via Ollama while the player is active, without blocking the main game loop.

Architecture:
  Main thread submits tasks → _task_queue
  Worker thread processes tasks → calls Ollama → puts results in _results queue
  Main thread polls _results each turn (non-blocking) and integrates content

Result dict shapes:
  {"type": "quest",      "template": QuestTemplate}
  {"type": "narrative",  "zone_id": str, "text": str}
  {"type": "npc_branch", "npc_id": str, "node": dict}
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai.content_generator import ContentGenerator
    from entities.quest import QuestTemplate

logger = logging.getLogger(__name__)


class BackgroundGenerator:
    """Manages a single daemon thread for background AI content generation."""

    def __init__(self, content_generator: "ContentGenerator") -> None:
        self._content_gen = content_generator
        self._task_queue: queue.Queue[dict | None] = queue.Queue(maxsize=3)
        self._results: queue.Queue[dict] = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="AI-BG"
        )
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._thread.start()
            logger.info("BackgroundGenerator started.")

    def stop(self) -> None:
        self._running = False
        try:
            self._task_queue.put_nowait(None)  # sentinel to unblock worker
        except queue.Full:
            pass

    # ── Task submission ────────────────────────────────────────────────────────

    def submit_quest(self, zone_id: str, npc_hint: str, player_profile: dict) -> bool:
        """Queue a background quest generation. Returns False if queue full."""
        return self._submit({
            "type": "quest",
            "zone_id": zone_id,
            "npc_hint": npc_hint,
            "player_profile": player_profile,
        })

    def submit_zone_narrative(self, zone_id: str, zone_name: str, context_flags: list[str]) -> bool:
        """Queue generation of richer entrance text for an unexplored zone."""
        return self._submit({
            "type": "narrative",
            "zone_id": zone_id,
            "zone_name": zone_name,
            "context_flags": context_flags,
        })

    def submit_npc_branch(self, npc_id: str, npc_name: str, player_profile: dict) -> bool:
        """Queue generation of a new dialogue branch for an NPC."""
        return self._submit({
            "type": "npc_branch",
            "npc_id": npc_id,
            "npc_name": npc_name,
            "player_profile": player_profile,
        })

    def _submit(self, task: dict) -> bool:
        try:
            self._task_queue.put_nowait(task)
            return True
        except queue.Full:
            return False

    # ── Result polling ─────────────────────────────────────────────────────────

    def poll_results(self) -> list[dict]:
        """Non-blocking drain of ready results. Returns list of result dicts."""
        results = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                break
        return results

    # ── Worker (runs in daemon thread) ─────────────────────────────────────────

    def _worker(self) -> None:
        logger.info("AI background worker started.")
        while self._running:
            try:
                task = self._task_queue.get(timeout=2.0)
                if task is None:  # sentinel — stop signal
                    break
                result = self._process_task(task)
                if result:
                    try:
                        self._results.put_nowait(result)
                    except queue.Full:
                        pass  # discard if results queue full
                self._task_queue.task_done()
            except queue.Empty:
                continue  # timeout — loop and check _running again
            except Exception as exc:
                logger.warning(f"BG generation failed: {exc}")
        logger.info("AI background worker stopped.")

    def _process_task(self, task: dict) -> dict | None:
        t = task.get("type")
        if t == "quest":
            return self._gen_quest(task)
        if t == "narrative":
            return self._gen_narrative(task)
        if t == "npc_branch":
            return self._gen_npc_branch(task)
        return None

    def _gen_quest(self, task: dict) -> dict | None:
        try:
            from ai.prompt_builder import build_quest_generation_prompt
            zone_id = task.get("zone_id", "unknown_zone")
            npc_hint = task.get("npc_hint", "a mysterious stranger")
            player_profile = task.get("player_profile", {})
            lore_data = getattr(self._content_gen, "lore_data", {})

            prompt = build_quest_generation_prompt(
                player_profile=player_profile,
                giver_npc_id=None,
                npc_name=npc_hint,
                npc_role="stranger",
                lore_data=lore_data,
            )
            template = self._content_gen.generate_quest(
                player_profile=player_profile,
                giver_npc_id=None,
                npc_name=npc_hint,
                npc_role="stranger",
            )
            if template:
                return {"type": "quest", "template": template}
        except Exception as exc:
            logger.warning(f"BG quest generation error: {exc}")
        return None

    def _gen_narrative(self, task: dict) -> dict | None:
        try:
            zone_id = task.get("zone_id", "")
            zone_name = task.get("zone_name", zone_id)
            context_flags = task.get("context_flags", [])
            lore_data = getattr(self._content_gen, "lore_data", {})

            context_str = ", ".join(context_flags) if context_flags else "no special context"
            system_prompt = (
                "You are the narrator of Aethoria, a world reshaped by a reality-fracturing event called "
                "the Fracture. The System — an ancient machine intelligence — now governs class assignments, "
                "life tokens, and death records. Write vivid, terse location descriptions (2-4 sentences). "
                "No lists. No meta-commentary."
            )
            user_prompt = (
                f"Write a short atmospheric entrance description for the zone: {zone_name}. "
                f"Context flags active: {context_str}. "
                "Keep it under 60 words. Match the tone: grim, curious, layered with history."
            )
            text = self._content_gen.client.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            if text:
                return {"type": "narrative", "zone_id": zone_id, "text": text.strip()}
        except Exception as exc:
            logger.warning(f"BG narrative generation error: {exc}")
        return None

    def _gen_npc_branch(self, task: dict) -> dict | None:
        try:
            npc_id = task.get("npc_id", "")
            npc_name = task.get("npc_name", "Stranger")
            player_profile = task.get("player_profile", {})

            system_prompt = (
                "You are the NPC dialogue writer for Aethoria. "
                "Generate a short NPC dialogue node as JSON with keys: "
                "node_id (string), text (string, max 80 words), "
                "options (array of 1-2 objects each with option_id, label, npc_response, leads_to_node='__exit__')."
            )
            user_prompt = (
                f"Generate a new dialogue branch for NPC '{npc_name}' (id: {npc_id}). "
                f"Player context: level {player_profile.get('level', 1)}, "
                f"alignment {player_profile.get('alignment', 0):.0f}, "
                f"class {player_profile.get('active_class', 'Unclassified')}. "
                "The dialogue should be a short side comment relevant to current world events."
            )
            result = self._content_gen.client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            if result and "node_id" in result and "text" in result:
                return {"type": "npc_branch", "npc_id": npc_id, "node": result}
        except Exception as exc:
            logger.warning(f"BG NPC branch generation error: {exc}")
        return None
