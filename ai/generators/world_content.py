"""
WorldContentGenerators mixin — atmospheric and world-state content.

Covers: world events, rumors, lore entries, area activity, zone narrative,
and the autonomous free-generation expansion slot.

All methods run in the BackgroundGenerator worker thread.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class WorldContentGenerators:
    """Mixin providing world-content generation methods for BackgroundGenerator."""

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

    def _gen_narrative(self, task: dict) -> dict | None:
        try:
            zone_id       = task.get("zone_id", "")
            zone_name     = task.get("zone_name", zone_id)
            context_flags = task.get("context_flags", [])
            context_str   = ", ".join(context_flags) if context_flags else "no special context"
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
