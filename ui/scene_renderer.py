"""
Render the current scene to the terminal.

Extracted from ``core/game_engine.py._render_scene``. Handles every visual
element shown per turn before the input prompt:

  - Clear + title bar
  - Status bar (skipped on intro scenes)
  - Scene header
  - Entrance text (once per scene visit)
  - Current node text (or full status panel on the status_check node)
  - "Also here:" line for bots in the player's current zone

Pure presentation — no game-state mutation, no system calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config import feature
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine


# Scenes where the status bar is hidden (intro framing).
_INTRO_SCENES = ("prologue", "character_creation")


def render_scene(engine: "GameEngine") -> None:
    """Render the player's current scene + node."""
    renderer.clear()
    renderer.print_title()

    scene = engine.scene_registry.get(engine.state.current_scene_id)
    if not scene:
        renderer.print_error(f"Scene not found: {engine.state.current_scene_id}")
        return

    if engine.state.current_scene_id not in _INTRO_SCENES:
        renderer.print_status_bar(engine.state.player, engine.item_registry)
        renderer.print_divider()

    renderer.print_scene_header(scene.title)

    node_id   = engine.state.current_node_id
    scene_key = engine.state.current_scene_id

    # Entrance text — shown once per scene-key, then suppressed
    if (
        node_id == "root"
        and scene.entrance_text
        and scene_key not in engine.state._entrance_shown
    ):
        engine.state._entrance_shown.add(scene_key)
        renderer.print_scene_text(scene.entrance_text, pause_between=0.05)
        renderer.print_divider()

    node = scene.get_node(node_id)
    node_text = node.get("text", "")
    if node_text:
        if node_id == "status_check":
            renderer.print_full_status(
                engine.state.player, engine.item_registry, engine.skill_registry,
            )
        else:
            renderer.print_scene_text([node_text])

    _render_bots_present(engine)


def _render_bots_present(engine: "GameEngine") -> None:
    """If the bot system is on, show a one-line list of bots in this zone."""
    if not (feature("bot_system") and engine._bot_manager):
        return
    bots_here = [
        b for b in engine._bot_manager.all()
        if b.current_zone_id == engine.state.current_scene_id
    ]
    if bots_here:
        names = ", ".join(b.name for b in bots_here)
        renderer.console.print(f"  [dim_text]Also here: {names}[/dim_text]")
