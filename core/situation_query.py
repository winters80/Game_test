"""
Handle the player's natural-language "« Ask about this situation »" prompt.

The AI takes the current scene + node text + the player's typed question and
returns a short narrative plus 0–4 generated SceneOptions that get injected
into the current node (and persist via state._dynamic_options).

Extracted from ``core/game_engine.py`` as a single free function so the engine
class doesn't have to own the 130-line orchestration of:
  - typing prompt
  - building scene-context payload
  - calling the AI under a spinner with logged error reporting
  - converting AI options to SceneOptions with gate evaluation
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import questionary

from scenes.scene_base import SceneOption
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


def handle_situation_query(engine: "GameEngine", current_options: list) -> None:
    """Prompt the player for a free-text question, call the AI, inject options."""
    ai_service = getattr(engine, "ai_service", None)
    if ai_service is None or not ai_service.is_available:
        renderer.print_system_message(
            "The System is silent. (AI offline — enable Ollama to use this feature.)",
            style="dim_text",
        )
        renderer.prompt_any_key()
        return

    renderer.console.print()
    question = questionary.text(
        "What do you want to know or try?",
        instruction=(
            "(e.g. 'Can I aim for the head?' or 'Is there a way to sneak past?'. "
            "Leave blank or type 'cancel' to back out.)"
        ),
    ).ask()
    # Accept blank input, an explicit 'cancel', or a couple of common
    # back-out phrases as the abort path. Avoids the awkward situation
    # where a player tries to abort mid-typing and ends up submitting
    # gibberish to the AI.
    if (
        not question
        or not question.strip()
        or question.strip().lower() in {"cancel", "back", "nevermind", "never mind", "stop"}
    ):
        return

    question = question.strip()
    logger.info(
        "Dynamic query: scene=%s node=%s question=%r",
        engine.state.current_scene_id, engine.state.current_node_id, question,
    )

    scene = engine.scene_registry.get(engine.state.current_scene_id)
    scene_title = scene.title if scene else engine.state.current_scene_id
    node = scene.get_node(engine.state.current_node_id) if scene else {}
    scene_text = node.get("text", "")
    if not scene_text and scene and scene.entrance_text:
        scene_text = " ".join(scene.entrance_text[:2])

    option_labels = [opt.label for opt in current_options if not opt.locked]

    # AIService logs and swallows generation failures and returns None,
    # which lands in the "could not generate a response" path below.
    with renderer.show_ai_thinking_spinner(f"ANALYZING: {question[:40]}..."):
        result = ai_service.generate_dynamic_options(
            question=question,
            scene_title=scene_title,
            scene_text=scene_text,
            current_options=option_labels,
            player=engine.state.player,
        )

    if not result:
        logger.info(
            "Dynamic query returned no result: scene=%s question=%r",
            engine.state.current_scene_id, question,
        )
        renderer.print_system_message(
            "The System could not generate a response. Try rephrasing your question.",
            style="dim_text",
        )
        renderer.prompt_any_key()
        return

    # Quality guard: if the AI returned a near-empty situation_text AND no
    # new options, treat it as a non-answer. The player typed something the
    # model didn't understand; nudge them to rephrase instead of showing
    # a one-liner that looks like a non-sequitur ("The light intensifies...").
    situation = (result.situation_text or "").strip()
    if not result.options and len(situation) < 60:
        logger.info(
            "Dynamic query response too thin (len=%d, options=0): scene=%s question=%r",
            len(situation), engine.state.current_scene_id, question,
        )
        renderer.print_system_message(
            "The System received your question but could not draw any new path "
            "from it. Try a more specific or in-character phrasing.",
            style="dim_text",
        )
        renderer.prompt_any_key()
        return

    renderer.print_divider()
    renderer.print_scene_text([situation or "..."])
    renderer.print_divider()

    if not result.options:
        renderer.print_system_message(
            "No new options could be generated for that question.",
            style="dim_text",
        )
        renderer.prompt_any_key()
        return

    new_scene_options = [_convert_ai_option(ai_opt, engine) for ai_opt in result.options]

    state_key = f"{engine.state.current_scene_id}:{engine.state.current_node_id}"
    engine.state._dynamic_options[state_key] = new_scene_options

    renderer.console.print("  [dim_text]New options unlocked. Choose below.[/dim_text]")
    renderer.prompt_any_key()


def _convert_ai_option(ai_opt, engine: "GameEngine") -> SceneOption:
    """Build a SceneOption from an AI option, evaluating min_stats / flags gates.

    The option's triggers pass through the AI trigger policy first, so the
    model can only attach allow-listed effects (see systems/ai_trigger_policy).
    """
    from systems.ai_trigger_policy import (
        WORLD_ACTION_PREFIX, ai_required_flag_met, sanitize_ai_triggers,
    )
    from systems.world_growth import derive_world_action

    requires = ai_opt.requires or {}
    locked = False
    lock_reason = ""
    # Every typed action may grow the world. The tag always comes from the
    # option's label, never from the model: a small model tags options with
    # whatever example it saw ("mine:gold") rather than what the option does.
    raw_triggers = [t for t in (ai_opt.triggers or []) if not str(t).startswith(WORLD_ACTION_PREFIX)]
    derived = derive_world_action(ai_opt.label)
    if derived:
        raw_triggers.append(derived)
    triggers = sanitize_ai_triggers(
        raw_triggers, getattr(engine, "item_registry", None),
    ).kept

    for stat, val in requires.get("min_stats", {}).items():
        if getattr(engine.state.player.stats, stat, 0) < val:
            locked = True
            lock_reason = f"Requires {stat} >= {val}"
            break

    if not locked:
        for flag in requires.get("flags", []):
            if not ai_required_flag_met(engine.state.player.has_flag, flag):
                locked = True
                lock_reason = "Condition not met"
                break

    return SceneOption(
        option_id=ai_opt.option_id,
        label=f"[AI] {ai_opt.label}",
        leads_to="__stay__",
        leads_to_node=engine.state.current_node_id,
        expected=False,  # always a divergence signal
        triggers=triggers,
        locked=locked,
        lock_reason=lock_reason,
        narrative=ai_opt.narrative,
    )
