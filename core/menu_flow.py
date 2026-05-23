"""
Main-menu / Load-game / New-game flow.

Extracted from ``core/game_engine.py``. These three functions handle the
"before the game loop starts" phase: pick a save or start fresh, set up
``engine.state``, then hand off to ``engine._game_loop()``.

They take the engine as a parameter and mutate ``engine.state`` /
``engine.skill_registry`` etc. directly — they're conductors, not owners
of state.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import questionary

from config import SAVES_DIR
from entities.player import Player
from persistence.save_manager import list_saves, load_game, new_game_state
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine


def main_menu(engine: "GameEngine") -> None:
    """Show the title-screen menu and dispatch to New Game / Load Game / Quit."""
    saves = list_saves(SAVES_DIR)
    choices = ["New Game"]
    if saves:
        choices.append("Load Game")
    choices.append("Quit")

    choice = questionary.select("", choices=choices).ask()

    if choice == "New Game":
        new_game(engine)
    elif choice == "Load Game":
        load_game_menu(engine, saves)
    else:
        renderer.console.print("\n  [dim_text]Farewell.[/dim_text]\n")


def load_game_menu(engine: "GameEngine", saves: list[str]) -> None:
    """Pick a save slot and start the game loop. Falls back to main menu on error."""
    choice = questionary.select("Select save:", choices=saves + ["← Back"]).ask()
    if choice == "← Back":
        main_menu(engine)
        return

    state = load_game(choice, SAVES_DIR)
    if state is None:
        renderer.print_error("Failed to load save.")
        main_menu(engine)
        return

    engine.state = state
    engine.state.skill_registry = engine.skill_registry
    # Re-register any AI-generated skills stored in the world DB
    for defn in getattr(engine.state, "_ai_skill_defs", []):
        from entities.skill import Skill
        skill = Skill.model_validate(defn)
        engine.skill_registry.register(skill)
    engine.state._ai_skill_defs = []

    renderer.print_success(f"Loaded save: {choice}")
    engine._game_loop()


def new_game(engine: "GameEngine") -> None:
    """Prompt for a name, create a fresh save slot, and start the game loop."""
    renderer.clear()
    renderer.print_system_message("WHAT IS YOUR NAME, ADVENTURER?")

    name = questionary.text("Name:").ask()
    if not name or not name.strip():
        name = "Wanderer"

    player = Player(name=name.strip())
    slot_name = re.sub(r'[^a-z0-9_]', '', player.name.lower().replace(" ", "_")) or "save"

    engine.state = new_game_state(player, SAVES_DIR, slot_name)
    engine.state.skill_registry = engine.skill_registry
    engine.state.current_scene_id = "prologue"
    engine.state.current_node_id = "root"

    renderer.print_success(f"Welcome, {player.name}.")
    time.sleep(0.5)
    engine._game_loop()
