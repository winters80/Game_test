"""
The Class Awakening: the System looks again at someone it couldn't classify.

Players who refuse the Rite (or otherwise end up unclassed) are offered the
``class_awakening`` trigger in the village. It scores their divergence
(systems/progression_tracker) and:

- divergent enough and the AI online → the primary model invents a class on
  a worker thread (``AIService.submit_class_generation_async``) while the
  spinner runs, so the game never freezes on the call
- otherwise → the nearest authored combo class, or ``fallback_generated``
  (the Wayward Soul) for someone with no class at all

The class is assigned (as the base class if they had none, else as their
active class with its starting skills), revealed, and ``class_awakened`` set.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import OLLAMA_TIMEOUT_JSON
from core.event_bus import Event, bus
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)

AWAKENING_TIMEOUT = max(60, OLLAMA_TIMEOUT_JSON * 2)   # class + its skills


def awaken(engine: "GameEngine"):
    """Run the awakening. Returns the class assigned, or None."""
    from ai.ai_service import AIService
    from systems import class_system
    from systems.progression_tracker import compute_divergence_score

    player = engine.state.player
    if player.has_flag("class_awakened"):
        renderer.print_system_message("The System has already looked at you. It isn't looking again.",
                                      style="dim_text")
        return None
    div = compute_divergence_score(player, engine.class_registry, engine.item_registry)
    renderer.print_system_message(f"DIVERGENCE ANALYSIS · SCORE {div.score}", style="system_warning")

    cls = None
    if div.trigger_ai and engine._ai_online():
        bus.publish(Event("ANOMALY_DETECTED", {}))
        bus.flush()
        future = engine.ai_service.submit_class_generation_async(
            player, div, engine.class_registry, skill_registry=engine.skill_registry)
        with renderer.show_ai_thinking_spinner("THE SYSTEM IS WRITING A CLASS THAT DID NOT EXIST..."):
            cls = AIService.await_class_result(future, timeout=AWAKENING_TIMEOUT)
        if cls is not None:
            engine.class_registry.register(cls)
    if cls is None:
        cls = class_system.resolve_combo_class(
            player, engine.class_registry, engine.item_registry, engine.skill_registry, ai_service=None)
    if cls is None:            # classless refusers: the authored "Wayward Soul"
        cls = engine.class_registry.get("fallback_generated")
    if cls is None:
        renderer.print_system_message("The System finds nothing to call you. Yet.", style="dim_text")
        return None

    if not player.base_class:
        class_system.assign_base_class(player, cls.class_id, engine.class_registry, engine.skill_registry)
    else:
        player.active_class = cls.class_id
        for sid in cls.starting_skills:
            if sid not in player.skills and engine.skill_registry.get(sid):
                player.skills.append(sid)
        bus.publish(Event("CLASS_ASSIGNED", {"class_id": cls.class_id, "class_name": cls.name,
                                             "rarity": getattr(cls.rarity, "value", str(cls.rarity))}))
    player.set_flag("class_awakened")
    engine.state.mark_dirty()
    bus.flush()
    renderer.print_class_reveal(cls)
    renderer.prompt_any_key()
    logger.info("Class awakening: %s (divergence %s, ai=%s)", cls.class_id, div.score, div.trigger_ai)
    return cls
