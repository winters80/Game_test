"""
Per-turn input: build the option list, present the questionary prompt,
dispatch the global hotkeys ([?], [K], [I], [J], [S], [A], [Q], [C], [L], [G]).

Extracted from ``core/game_engine.py``. The two functions here run once
per turn from the main loop:

  options = get_current_options(engine)
  choice  = prompt_choice(engine, options)

``prompt_choice`` either returns the chosen ``SceneOption`` (numeric pick),
or ``None`` after dispatching a hotkey to the appropriate engine handler.
A None return is the signal to the loop "I handled it — re-render".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import questionary

from config import feature
from scenes.scene_base import SceneOption
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine


def get_current_options(engine: "GameEngine") -> list[SceneOption]:
    """Build the option list for the current scene + node, including any
    AI-generated dynamic options previously stashed on state.
    """
    scene = engine.scene_registry.get(engine.state.current_scene_id)
    if not scene:
        return []
    options = scene.get_options(engine.state, engine.state.current_node_id)
    state_key = f"{engine.state.current_scene_id}:{engine.state.current_node_id}"
    dynamic = engine.state._dynamic_options.get(state_key, [])
    if dynamic:
        options = list(options) + dynamic
    return options


# ── Hotkey table ─────────────────────────────────────────────────────────────
#
# Each entry maps a key prefix ("[X]") to (engine_method_name, label).
# Kept as data so adding a hotkey is a single line in _build_extras() and
# _dispatch_hotkey() doesn't need a new branch.

_HOTKEYS: list[tuple[str, str, str]] = [
    # (key prefix, engine method, label)
    ("[?]", "_handle_situation_query", "Ask about this situation"),
    ("[K]", "_skills_menu",            "Skills"),
    ("[I]", "_inventory_menu",         "Items & Equipment"),
    ("[J]", "_quests_menu",            "Quest Journal"),
    ("[L]", "_lore_log",               "World Log"),
    ("[C]", "_craft_menu",             "Craft"),
    ("[S]", "_save_prompt",            "Save game"),
    ("[A]", "_admin_panel",            "Admin Panel"),
    ("[G]", "_found_guild_menu",       "Found a Guild"),
    ("[Q]", "__quit__",                "Quit to menu"),
]


def _build_extras(engine: "GameEngine") -> list[str]:
    """Return the labelled hotkey list, filtered by feature flags + state.

    Order is preserved relative to _HOTKEYS so the menu layout stays stable
    across turns regardless of which optional items are present.
    """
    out: list[str] = []
    for key, method, label in _HOTKEYS:
        if key == "[C]":
            if not feature("crafting_system"):
                continue
            player = engine.state.player
            if not (player.has_flag("alchemist") or player.has_flag("crafter")):
                continue
        elif key == "[J]":
            if not (feature("quest_system") and engine.quest_registry):
                continue
        elif key == "[G]":
            if not feature("guild_system"):
                continue
        out.append(f"{key} {label}")
    return out


def prompt_choice(engine: "GameEngine", options: list[SceneOption]) -> SceneOption | None:
    """Render options, prompt the player, dispatch hotkeys, or return a chosen option."""
    renderer.print_options(options)

    available = [(i + 1, opt) for i, opt in enumerate(options) if not opt.locked]
    if not available:
        renderer.print_error("All options are locked.")
        return None

    extras = _build_extras(engine)
    choice_labels = [f"{i}. {opt.label}" for i, opt in available] + extras
    answer = questionary.select("Choose:", choices=choice_labels).ask()

    if answer is None:
        engine._running = False
        return None

    # ── Hotkey dispatch ─────────────────────────────────────────────────
    for key, method, _label in _HOTKEYS:
        if not answer.startswith(key):
            continue
        if method == "__quit__":
            engine._running = False
        elif method == "_handle_situation_query":
            # Only hotkey that takes an argument
            getattr(engine, method)(options)
        else:
            getattr(engine, method)()
        return None

    # ── Numeric pick ────────────────────────────────────────────────────
    try:
        num = int(answer.split(".")[0])
    except (ValueError, IndexError):
        return None
    for i, opt in available:
        if i == num:
            return opt
    return None
