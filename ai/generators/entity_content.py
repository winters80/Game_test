"""
EntityContentGenerators mixin — NPC, quest, bot, and guild content.

Covers: quest generation, NPC dialogue branches, bot action decisions,
and guild simulation ticks.

All methods run in the BackgroundGenerator worker thread.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EntityContentGenerators:
    """Mixin providing entity-content generation methods for BackgroundGenerator."""

    def _gen_quest(self, task: dict) -> dict | None:
        try:
            npc_hint = task.get("npc_hint", "a mysterious stranger")
            player   = task.get("player")
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

    def _gen_npc_branch(self, task: dict) -> dict | None:
        try:
            npc_id         = task.get("npc_id", "")
            npc_name       = task.get("npc_name", "Stranger")
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
            bot_id  = task.get("bot_id", "")
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

    def _gen_guild_tick(self, task: dict) -> dict | None:
        """Run one guild simulation tick in the background thread."""
        try:
            from systems.guilds import guild_sim
            from ai.ai_service import AIService
            world_db       = task.get("world_db")
            guild_registry = task.get("guild_registry")
            if world_db is None:
                return None
            turn = self._autonomous_context.get("turn", 0)
            # Wrap the content generator in an AIService so guild_sim sees a
            # consistent boundary regardless of whether this runs on the BG
            # thread or is called directly from the engine in a test.
            ai_service = AIService(content_generator=self._content_gen)
            results = guild_sim.tick(
                world_db=world_db,
                guild_registry=guild_registry,
                ai_service=ai_service,
                turn=turn,
            )
            if results:
                return {"type": "guild_tick_results", "results": results}
        except Exception as exc:
            logger.warning(f"BG guild tick error: {exc}")
        return None
