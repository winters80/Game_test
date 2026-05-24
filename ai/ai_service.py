"""
AIService — single facade non-AI packages depend on when they need AI output.

Why this exists:
  Before AIService, systems (class_system, quest_system, guilds/guild_sim) each
  imported a ContentGenerator and called its methods directly. That broke the
  CLAUDE.md rule "systems are pure — no I/O, no direct Ollama calls" and
  scattered the try/except + fallback pattern across many files.

AIService is the ONLY entry point non-AI code should use for AI generation.
Every method:
  - Returns None on failure (caller is expected to have a fallback path)
  - Centralises exception handling + logging
  - Returns None immediately if AI is unavailable (no ContentGenerator)

The optional ``bg_generator`` reference is for future async submission
(``submit_quest_async`` and friends). The synchronous methods today still
block — the cleanup is the boundary, not the threading. Once an interactive
path can tolerate waiting one extra frame, swap to the *_async variant and
poll its result in the engine's normal background-result drain.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai.content_generator import ContentGenerator
    from ai.background_generator import BackgroundGenerator
    from entities.character_class import ClassDefinition, ClassRegistry
    from entities.player import Player
    from entities.quest import QuestTemplate
    from systems.progression_tracker import DivergenceResult

logger = logging.getLogger(__name__)


class AIService:
    """Thin, side-effect-free wrapper around ContentGenerator + BackgroundGenerator."""

    def __init__(
        self,
        content_generator: "ContentGenerator | None" = None,
        background_generator: "BackgroundGenerator | None" = None,
    ) -> None:
        self._gen = content_generator
        self._bg = background_generator
        # Lazy: only spawn the thread pool if the caller actually uses the
        # async class-generation path. Keeps a tight idle footprint when AI
        # is unavailable.
        self._class_executor: "ThreadPoolExecutor | None" = None

    def shutdown(self) -> None:
        """Release the class-gen executor thread. Safe to call multiple times."""
        if self._class_executor is not None:
            self._class_executor.shutdown(wait=False, cancel_futures=True)
            self._class_executor = None

    # ── Availability ─────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True if a ContentGenerator is wired up. False = always fall back."""
        return self._gen is not None

    @property
    def has_background(self) -> bool:
        """True if async submission via the BG queue is possible."""
        return self._bg is not None

    # ── Class generation ─────────────────────────────────────────────────────

    def generate_class(
        self,
        player: "Player",
        divergence: "DivergenceResult",
        class_registry: "ClassRegistry",
        skill_registry: Any = None,
        rich_skills: bool = False,
    ) -> "ClassDefinition | None":
        if self._gen is None:
            return None
        try:
            return self._gen.generate_class(
                player, divergence, class_registry,
                skill_registry=skill_registry,
                rich_skills=rich_skills,
            )
        except Exception:
            logger.warning("AI class generation failed", exc_info=True)
            return None

    def submit_class_generation_async(
        self,
        player: "Player",
        divergence: "DivergenceResult",
        class_registry: "ClassRegistry",
    ) -> "Future[ClassDefinition | None] | None":
        """
        Run AI class generation on a worker thread. Returns a Future the
        caller can wait on with a timeout, or None if AI is unavailable.

        Wait on the future with ``await_class_result()`` rather than calling
        ``.result(timeout)`` directly — the helper centralises the
        spinner / timeout / cancel handling.
        """
        if self._gen is None:
            return None
        if self._class_executor is None:
            self._class_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="ai-class",
            )
        return self._class_executor.submit(
            self.generate_class, player, divergence, class_registry,
        )

    @staticmethod
    def await_class_result(
        future: "Future[ClassDefinition | None] | None",
        timeout: float = 60.0,
    ) -> "ClassDefinition | None":
        """
        Block the calling thread until the class-generation future resolves or
        the timeout expires. Returns the generated class, or None if the AI
        failed, timed out, or was never available.

        The Rich spinner (started by the caller) keeps animating during the
        wait because ``rich.Live`` refreshes from its own internal thread.
        """
        if future is None:
            return None
        try:
            return future.result(timeout=timeout)
        except Exception:
            logger.warning("AI class generation timed out or failed", exc_info=True)
            future.cancel()
            return None

    # ── Skill generation (contextual) ────────────────────────────────────────

    def generate_skill(
        self,
        player: "Player",
        name_hint: str,
        source: str,
        context: dict | None = None,
        has_inspect: bool = False,
    ) -> Any:
        """Generate a single skill in context. Returns None if AI is offline,
        the generator fails, or the response doesn't validate.

        Callers (give_skill: trigger, quest reward path, future inspect /
        trainer flows) should fall back to a deterministic default Skill
        when this returns None.
        """
        if self._gen is None:
            return None
        try:
            return self._gen.generate_skill(
                player=player,
                name_hint=name_hint,
                source=source,
                context=context or {},
                has_inspect=has_inspect,
            )
        except Exception:
            logger.warning("AI skill generation failed", exc_info=True)
            return None

    # ── Quest generation ─────────────────────────────────────────────────────

    def generate_quest(
        self,
        player: "Player",
        giver_npc_id: str,
        npc_name: str,
        npc_role: str,
    ) -> "QuestTemplate | None":
        if self._gen is None:
            return None
        try:
            return self._gen.generate_quest(player, giver_npc_id, npc_name, npc_role)
        except Exception:
            logger.warning("AI quest generation failed", exc_info=True)
            return None

    def submit_quest_async(self, zone_id: str, npc_hint: str, player: Any) -> bool:
        """Queue a quest generation on the BG thread. False = unavailable / queue full."""
        if self._bg is None:
            return False
        try:
            return self._bg.submit_quest(zone_id=zone_id, npc_hint=npc_hint, player=player)
        except Exception:
            logger.warning("AI quest async submission failed", exc_info=True)
            return False

    # ── Guild generation ─────────────────────────────────────────────────────

    def generate_guild_intent(self, guild: object, member: object) -> object | None:
        if self._gen is None:
            return None
        try:
            return self._gen.generate_guild_intent(guild, member)
        except Exception:
            logger.warning("AI guild intent generation failed", exc_info=True)
            return None

    def generate_guild_template(
        self,
        name: str,
        archetype: str,
        zone_id: str,
        founding_reason: str = "",
        seed_traits: list[str] | None = None,
    ) -> object | None:
        if self._gen is None:
            return None
        try:
            return self._gen.generate_guild_template(
                name=name,
                archetype=archetype,
                zone_id=zone_id,
                founding_reason=founding_reason,
                seed_traits=seed_traits,
            )
        except Exception:
            logger.warning("AI guild template generation failed", exc_info=True)
            return None

    # ── Narrative (text) ─────────────────────────────────────────────────────

    def generate_narrative(
        self,
        player: "Player",
        scene_id: str,
        choice_description: str,
        scene_tone: str = "mysterious",
        relevant_flags: list[str] | None = None,
    ) -> str | None:
        if self._gen is None:
            return None
        try:
            return self._gen.generate_narrative(
                player=player,
                scene_id=scene_id,
                choice_description=choice_description,
                scene_tone=scene_tone,
                relevant_flags=relevant_flags,
            )
        except Exception:
            logger.warning("AI narrative generation failed", exc_info=True)
            return None

    # ── Escape hatch ─────────────────────────────────────────────────────────

    @property
    def raw_generator(self) -> "ContentGenerator | None":
        """
        For interactive paths that need direct access (spinner control, streaming).
        New code should prefer the wrapped methods above; this exists so existing
        call sites in choice_handler / dialogue_handler don't need a rewrite just
        to add the facade.
        """
        return self._gen
