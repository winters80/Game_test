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
        import time as _time
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
            self._thread.start()
            logger.info("BackgroundGenerator started.")

    def stop(self) -> None:
        self._running = False
        try:
            self._task_queue.put_nowait(None)  # sentinel to unblock worker
        except queue.Full:
            pass

    # ── Task submission ────────────────────────────────────────────────────────

    def submit_quest(self, zone_id: str, npc_hint: str, player: Any) -> bool:
        """Queue a background quest generation. Returns False if queue full."""
        return self._submit({
            "type": "quest",
            "zone_id": zone_id,
            "npc_hint": npc_hint,
            "player": player,
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

    def update_context(self, zone_id: str, zone_name: str, player_level: int, flags: list[str], turn: int = 0) -> None:
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

        # Rotate through all content types on autonomous schedule
        # Use wall-clock modulo to pick type (varies each 20-min window)
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
                # Full director-style generation: ask Ollama what the world needs
                result = self._gen_autonomous_world_expansion(zone_id, zone_name, player_level, flags)

            if result:
                try:
                    self._results.put_nowait(result)
                    logger.info(f"Autonomous result queued: {result.get('type', '?')}")
                except queue.Full:
                    pass
        except Exception as exc:
            logger.warning(f"Autonomous generation error: {exc}")

    def _gen_autonomous_world_expansion(
        self, zone_id: str, zone_name: str, player_level: int, flags: list[str]
    ) -> dict | None:
        """
        Ask Ollama to autonomously decide what the world needs most right now
        and generate that content. This is the Director's 'free generation' slot.
        """
        try:
            flags_str = ", ".join(flags[:8]) if flags else "none"
            system_prompt = (
                "You are the living-world AI for Aethoria, a post-Fracture LitRPG. "
                "The System — an ancient machine intelligence that appeared during the Fracture — "
                "governs class assignments, life tracking, and death records. "
                "Your job is to generate one piece of world content that makes the world feel "
                "more alive. Generate a world event OR a rumour OR a lore fragment. "
                "Respond with ONLY a JSON object:\n"
                '{"type": "world_event"|"rumor"|"lore_entry", '
                '"event_text": "the content (max 60 words)", '
                '"zone_id": "zone_id_or_empty_string"}'
            )
            user_prompt = (
                f"Current zone: {zone_name} (id: {zone_id}). "
                f"Player level: {player_level}. "
                f"Active world context: {flags_str}.\n\n"
                "Choose whichever content type will best enrich this moment in the world. "
                "Be specific. Reference real in-world details. "
                "The Fracture happened 3 years ago. The System assigns classes. "
                "Verath is the capital city. The dungeon has unexplained anomalies on Floor 2."
            )
            result = self._content_gen.client.generate_json(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.88,
            )
            if result and "event_text" in result and "type" in result:
                rtype = result.get("type", "world_event")
                if rtype not in ("world_event", "rumor", "lore_entry"):
                    rtype = "world_event"
                return {
                    "type": rtype,
                    "zone_id": result.get("zone_id", zone_id),
                    "event_text": str(result["event_text"]).strip(),
                }
        except Exception as exc:
            logger.warning(f"Autonomous world expansion error: {exc}")
        return None

    def _process_task(self, task: dict) -> dict | None:
        t = task.get("type")
        if t == "quest":
            return self._gen_quest(task)
        if t == "narrative":
            return self._gen_narrative(task)
        if t == "npc_branch":
            return self._gen_npc_branch(task)
        if t == "bot_action":
            return self._gen_bot_action(task)
        if t == "world_event":      return self._gen_world_event(task)
        if t == "rumor":            return self._gen_rumor_task(task)
        if t == "lore_entry":       return self._gen_lore_entry(task)
        if t == "area_activity":    return self._gen_area_activity(task)
        if t == "guild_tick":       return self._gen_guild_tick(task)
        if t == "director_analysis": return self._gen_director_analysis(task)
        return None

    def _gen_director_analysis(self, task: dict) -> dict | None:
        """
        Run the World Director's LLM analysis in the background thread.
        Submitted by WorldDirector.tick() — never called on the main thread.
        """
        summary      = task.get("summary", {})
        fallback_flags = task.get("fallback_flags", [])
        zone_id      = task.get("zone_id", "")
        player_level = task.get("player_level", 1)

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
            self._submit({
                "type": "quest", "zone_id": zone,
                "npc_hint": goal[:60], "player": None,
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

    def _gen_quest(self, task: dict) -> dict | None:
        try:
            npc_hint = task.get("npc_hint", "a mysterious stranger")
            player = task.get("player")
            if player is None:
                return None
            template = self._content_gen.generate_quest(
                player=player,
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
                prompt=user_prompt,
                system_prompt=system_prompt,
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
                prompt=user_prompt,
                system_prompt=system_prompt,
            )
            if result and "node_id" in result and "text" in result:
                return {"type": "npc_branch", "npc_id": npc_id, "node": result}
        except Exception as exc:
            logger.warning(f"BG NPC branch generation error: {exc}")
        return None

    def _gen_bot_action(self, task: dict) -> dict | None:
        try:
            bot_id = task.get("bot_id", "")
            profile = task.get("bot_profile", {})
            context = task.get("world_context", {})
            system_prompt = (
                "You are the behaviour engine for autonomous NPC agents in Aethoria. "
                "Return JSON only: {\"type\": \"bot_action\", \"bot_id\": \"...\", \"action\": \"...\", \"target\": \"...\"}. "
                "Valid actions: move_zone, trade, talk_npc, rest, craft."
            )
            user_prompt = (
                f"Bot '{profile.get('name')}' (id: {bot_id}), personality: {profile.get('personality_seed')}. "
                f"Current goal: {profile.get('current_goal')}. Zone: {profile.get('current_zone_id')}. "
                f"World context: {context}. Decide the bot's next single action."
            )
            result = self._content_gen.client.generate_json(
                prompt=user_prompt,
                system_prompt=system_prompt,
            )
            if result and result.get("type") == "bot_action":
                return result
        except Exception as exc:
            logger.warning(f"BG bot action error: {exc}")
        return None

    def _gen_world_event(self, task: dict) -> dict | None:
        try:
            zone_name    = task.get("zone_name", "the city")
            player_level = task.get("player_level", 1)
            flags        = task.get("context_flags", [])
            flags_str    = ", ".join(flags[:6]) if flags else "none"
            system_prompt = (
                "You are the world event feed for Aethoria, a post-Fracture fantasy world. "
                "The System — an AI that assigns classes and tracks lives — governs society. "
                "Generate ONE brief world event (1-2 sentences, max 45 words). "
                "Be concrete, specific, atmospheric. No lists. No meta-commentary."
            )
            user_prompt = (
                f"Generate a world event near '{zone_name}'. "
                f"Player is level {player_level}. Active world flags: {flags_str}. "
                "Something specific just happened — a sighting, a development, a shift. "
                "Make it feel like the world is alive and changing."
            )
            text = self._content_gen.client.generate_text(
                prompt=user_prompt, system_prompt=system_prompt,
                temperature=0.9, max_tokens=80,
            )
            if text:
                return {
                    "type": "world_event",
                    "zone_id": task.get("zone_id", ""),
                    "event_text": text.strip(),
                }
        except Exception as exc:
            logger.warning(f"BG world_event error: {exc}")
        return None

    def _gen_rumor_task(self, task: dict) -> dict | None:
        try:
            flags     = task.get("context_flags", [])
            zone_id   = task.get("zone_id", "")
            flags_str = ", ".join(flags[:5]) if flags else "none"
            system_prompt = (
                "You write overheard gossip and rumours for Aethoria. "
                "One sentence only. Under 30 words. In the voice of a local resident — "
                "not omniscient, just what people are saying."
            )
            user_prompt = (
                f"Generate a rumour heard around {zone_id or 'the area'}. "
                f"World context flags: {flags_str}. "
                "Something a villager, guard, or merchant might whisper."
            )
            text = self._content_gen.client.generate_text(
                prompt=user_prompt, system_prompt=system_prompt,
                temperature=0.92, max_tokens=50,
            )
            if text:
                return {
                    "type": "rumor",
                    "zone_id": zone_id,
                    "event_text": text.strip(),
                }
        except Exception as exc:
            logger.warning(f"BG rumor error: {exc}")
        return None

    def _gen_lore_entry(self, task: dict) -> dict | None:
        try:
            flags     = task.get("context_flags", [])
            flags_str = ", ".join(flags[:6]) if flags else "none"
            system_prompt = (
                "You write lore fragments for Aethoria. "
                "Each fragment is 2-3 sentences from an in-world source: "
                "a torn journal page, a carved inscription, an overheard scholar. "
                "Tone: measured, a little unsettling. No titles in the text."
            )
            user_prompt = (
                f"Write a lore fragment about the world of Aethoria. "
                f"Relevant context flags: {flags_str}. "
                "It should reveal something small but interesting about the Fracture, "
                "the System, or the world before the Fracture."
            )
            text = self._content_gen.client.generate_text(
                prompt=user_prompt, system_prompt=system_prompt,
                temperature=0.88, max_tokens=100,
            )
            if text:
                return {
                    "type": "lore_entry",
                    "event_text": text.strip(),
                    "title": "Lore Fragment",
                }
        except Exception as exc:
            logger.warning(f"BG lore_entry error: {exc}")
        return None

    def _gen_guild_tick(self, task: dict) -> dict | None:
        """Run one guild simulation tick in the background thread."""
        try:
            from systems.guilds import guild_sim
            world_db = task.get("world_db")
            guild_registry = task.get("guild_registry")
            if world_db is None:
                return None
            turn = self._autonomous_context.get("turn", 0)
            results = guild_sim.tick(
                world_db=world_db,
                guild_registry=guild_registry,
                ai_generator=self._content_gen,
                turn=turn,
            )
            if results:
                return {"type": "guild_tick_results", "results": results}
        except Exception as exc:
            logger.warning(f"BG guild tick error: {exc}")
        return None

    def _gen_area_activity(self, task: dict) -> dict | None:
        try:
            zone_name = task.get("zone_name", "the area")
            zone_id   = task.get("zone_id", "")
            flags     = task.get("context_flags", [])
            flags_str = ", ".join(flags[:5]) if flags else "none"
            system_prompt = (
                "You describe background activity in specific zones of Aethoria. "
                "1-2 sentences. Concrete observable detail. Present tense. "
                "Things a sharp-eyed person would notice."
            )
            user_prompt = (
                f"Describe one thing currently happening in the background at '{zone_name}'. "
                f"World flags: {flags_str}. "
                "A small, specific detail — movement, sound, a person, an object. "
                "Not plot-critical, just texture."
            )
            text = self._content_gen.client.generate_text(
                prompt=user_prompt, system_prompt=system_prompt,
                temperature=0.87, max_tokens=70,
            )
            if text:
                return {
                    "type": "area_activity",
                    "zone_id": zone_id,
                    "event_text": text.strip(),
                }
        except Exception as exc:
            logger.warning(f"BG area_activity error: {exc}")
        return None
