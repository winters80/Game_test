"""
BackgroundGenerator — Daemon thread that autonomously generates game content
via Ollama while the player is active, without blocking the main game loop.

Architecture:
  Main thread submits tasks → _task_queue
  Worker thread processes tasks → calls Ollama → puts results in _results queue
  Main thread polls _results each turn (non-blocking) and integrates content

Result dict shapes:
  {"type": "quest",        "template": QuestTemplate}
  {"type": "narrative",    "zone_id": str, "text": str}
  {"type": "npc_branch",   "npc_id": str, "node": dict}
  {"type": "world_event",  "zone_id": str, "event_text": str}
  {"type": "rumor",        "zone_id": str, "event_text": str}
  {"type": "lore_entry",   "event_text": str, "title": str}
  {"type": "area_activity","zone_id": str, "event_text": str}
  {"type": "bot_action",   "bot_id": str, "action": str, "target": str}
  {"type": "director_fired","target_count": int}
  {"type": "world_expansion", "action_key": str, "zone_id": str,
   "response": AIWorldExpansionResponse, "skill": Skill | None}

Generation logic lives in ai/generators/ mixins:
  DirectorGenerators     — director analysis + target dispatch
  WorldContentGenerators — world events, rumors, lore, narrative, area activity
  EntityContentGenerators— quests, NPC branches, bot actions, guild ticks
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Any

from ai.generators import DirectorGenerators, WorldContentGenerators, EntityContentGenerators

if TYPE_CHECKING:
    from ai.content_generator import ContentGenerator
    from entities.quest import QuestTemplate

logger = logging.getLogger(__name__)


class BackgroundGenerator(DirectorGenerators, WorldContentGenerators, EntityContentGenerators):
    """
    Manages a single daemon thread for background AI content generation.

    All _gen_* methods are provided by the three Generator mixins above.
    This class owns only the queue, thread lifecycle, and task dispatch.
    """

    def __init__(self, content_generator: "ContentGenerator") -> None:
        self._content_gen = content_generator
        self._task_queue: queue.Queue[dict | None] = queue.Queue(maxsize=3)
        self._results: queue.Queue[dict] = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="AI-BG"
        )
        self._running = False
        self._last_autonomous_run: float = 0.0
        self._autonomous_interval: float = 1200.0  # 20 minutes
        self._autonomous_context: dict = {}  # updated by game engine each turn

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._running:
            self._running = True
            # Python threads cannot be restarted — always recreate
            if self._thread.is_alive():
                self._thread.join(timeout=2.0)  # wait briefly for old thread to exit
            self._thread = threading.Thread(
                target=self._worker, daemon=True, name="AI-BG"
            )
            self._thread.start()
            logger.info("BackgroundGenerator started.")

    def stop(self) -> None:
        self._running = False
        try:
            self._task_queue.put_nowait(None)  # sentinel to unblock worker
        except queue.Full:
            pass

    # ── Public task submission ─────────────────────────────────────────────────

    def submit_quest(self, zone_id: str, npc_hint: str, player: Any) -> bool:
        """Queue a background quest generation. Returns False if queue full."""
        return self._submit({
            "type": "quest",
            "zone_id": zone_id,
            "npc_hint": npc_hint,
            "player": player,
        })

    def submit_world_expansion(
        self,
        action_key: str,
        verb: str,
        subject: str,
        zone_id: str,
        zone_name: str,
        scene_title: str,
        narrative: str,
        player: Any,
        known_items: list[str],
    ) -> bool:
        """Queue a world expansion for a new player action. False if queue full."""
        return self._submit({
            "type": "world_expansion",
            "action_key": action_key,
            "verb": verb,
            "subject": subject,
            "zone_id": zone_id,
            "zone_name": zone_name,
            "scene_title": scene_title,
            "narrative": narrative,
            "player": player,
            "known_items": known_items,
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

    def submit_bot_decision(self, bot_id: str, bot_profile: dict, world_context: dict) -> bool:
        """Queue an AI decision for a bot agent. Returns False if queue full."""
        return self._submit({
            "type": "bot_action",
            "bot_id": bot_id,
            "bot_profile": bot_profile,
            "world_context": world_context,
        })

    def submit_world_event(
        self, zone_id: str, zone_name: str,
        player_level: int, context_flags: list[str],
    ) -> bool:
        """Queue generation of a world event relevant to the current zone."""
        return self._submit({
            "type": "world_event",
            "zone_id": zone_id,
            "zone_name": zone_name,
            "player_level": player_level,
            "context_flags": context_flags,
        })

    def submit_rumor(
        self, zone_id: str, context_flags: list[str], player_level: int,
    ) -> bool:
        """Queue generation of a local rumor or piece of gossip."""
        return self._submit({
            "type": "rumor",
            "zone_id": zone_id,
            "context_flags": context_flags,
            "player_level": player_level,
        })

    def submit_lore_entry(
        self, context_flags: list[str], player_flags: list[str],
    ) -> bool:
        """Queue generation of a lore fragment."""
        return self._submit({
            "type": "lore_entry",
            "context_flags": context_flags,
            "player_flags": player_flags,
        })

    def submit_area_activity(
        self, zone_id: str, zone_name: str, context_flags: list[str],
    ) -> bool:
        """Queue generation of moment-to-moment area activity."""
        return self._submit({
            "type": "area_activity",
            "zone_id": zone_id,
            "zone_name": zone_name,
            "context_flags": context_flags,
        })

    def submit_guild_tick(self, world_db: object, guild_registry: object) -> bool:
        """Queue a guild simulation tick. Returns False if queue full."""
        return self._submit({
            "type": "guild_tick",
            "world_db": world_db,
            "guild_registry": guild_registry,
        })

    def update_context(
        self, zone_id: str, zone_name: str,
        player_level: int, flags: list[str], turn: int = 0,
    ) -> None:
        """Update the autonomous generator's world context. Called from game loop."""
        self._autonomous_context = {
            "zone_id": zone_id,
            "zone_name": zone_name,
            "player_level": player_level,
            "flags": flags,
            "turn": turn,
        }

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
        import time as _time
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
                # No submitted tasks — check if autonomous generation is due
                self._maybe_autonomous_generate()
                continue
            except Exception as exc:
                logger.warning(f"BG generation failed: {exc}")
        logger.info("AI background worker stopped.")

    def _maybe_autonomous_generate(self) -> None:
        """
        Fires autonomous world generation every _autonomous_interval seconds,
        completely independent of player actions or turn count.
        Called from worker thread when task queue is empty.
        """
        import time as _time
        now = _time.monotonic()
        if now - self._last_autonomous_run < self._autonomous_interval:
            return
        self._last_autonomous_run = now

        ctx = self._autonomous_context
        if not ctx:
            return

        zone_id      = ctx.get("zone_id", "")
        zone_name    = ctx.get("zone_name", zone_id)
        player_level = ctx.get("player_level", 1)
        flags        = ctx.get("flags", [])

        # Rotate through content types — use wall-clock modulo (varies each window)
        slot = int(_time.monotonic() / self._autonomous_interval) % 6

        logger.info(f"Autonomous generation firing: slot={slot}, zone={zone_id}")

        try:
            if slot == 0:
                result = self._gen_world_event({
                    "zone_id": zone_id, "zone_name": zone_name,
                    "player_level": player_level, "context_flags": flags,
                })
            elif slot == 1:
                result = self._gen_rumor_task({
                    "zone_id": zone_id, "context_flags": flags, "player_level": player_level,
                })
            elif slot == 2:
                result = self._gen_lore_entry({
                    "context_flags": flags, "player_flags": flags,
                })
            elif slot == 3:
                result = self._gen_area_activity({
                    "zone_id": zone_id, "zone_name": zone_name, "context_flags": flags,
                })
            elif slot == 4:
                result = self._gen_narrative({
                    "zone_id": zone_id, "zone_name": zone_name, "context_flags": flags,
                })
            else:
                # Free-form: ask Ollama what the world needs most right now
                result = self._gen_autonomous_world_expansion(zone_id, zone_name, player_level, flags)

            if result:
                try:
                    self._results.put_nowait(result)
                    logger.info(f"Autonomous result queued: {result.get('type', '?')}")
                except queue.Full:
                    pass
        except Exception as exc:
            logger.warning(f"Autonomous generation error: {exc}")

    # ── Task dispatcher ────────────────────────────────────────────────────────

    def _process_task(self, task: dict) -> dict | None:
        t = task.get("type")
        if t == "quest":          return self._gen_quest(task)
        if t == "narrative":      return self._gen_narrative(task)
        if t == "npc_branch":     return self._gen_npc_branch(task)
        if t == "bot_action":     return self._gen_bot_action(task)
        if t == "world_event":    return self._gen_world_event(task)
        if t == "rumor":          return self._gen_rumor_task(task)
        if t == "lore_entry":     return self._gen_lore_entry(task)
        if t == "area_activity":  return self._gen_area_activity(task)
        if t == "guild_tick":     return self._gen_guild_tick(task)
        if t == "director_analysis": return self._gen_director_analysis(task)
        if t == "world_expansion": return self._gen_world_expansion(task)
        return None
