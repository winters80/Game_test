"""
GameEngine — composition root.

After the refactor series this file is a thin coordinator:
  - holds registries + live GameState
  - calls bootstrap helpers to load data + wire AI on startup
  - exposes ``_*`` methods that delegate to focused modules under core/, ui/,
    and systems/ so each concern can be read in isolation

Concerns extracted to their own modules:
  core/bootstrap.py           — load_registries + setup_ai
  core/menu_flow.py           — main menu / load / new game
  core/game_loop.py           — per-turn loop + tick orchestrator
  core/input_handler.py       — option building + hotkey dispatch
  core/situation_query.py     — «Ask about this situation» AI handler
  core/background_integrator.py — BG result drain
  core/bg_scheduler.py        — decides which AI content to submit each turn
  ui/scene_renderer.py        — per-turn scene rendering
  ui/auction_ui.py            — interactive auction house loop
  systems/rest_system.py      — camp rest (full / short)
  systems/faction_endings.py  — political ending path triggers
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from config import feature
from core.combat_handler import CombatHandlerMixin
from core.choice_handler import ChoiceHandlerMixin
from core.dialogue_handler import DialogueHandlerMixin
from core.event_bus import bus
from core.menus import GameMenusMixin
from core.state_manager import GameState
from entities.character_class import ClassRegistry
from entities.item import ItemRegistry
from entities.skill import SkillRegistry
from scenes.scene_base import SceneOption
from scenes.scene_registry import SceneRegistry
from ui import renderer
from ui.notifications import setup_notification_listeners


class GameEngine(GameMenusMixin, CombatHandlerMixin, DialogueHandlerMixin, ChoiceHandlerMixin):
    def __init__(self) -> None:
        # Registries — always present, populated by bootstrap.load_registries
        self.class_registry = ClassRegistry()
        self.skill_registry = SkillRegistry()
        self.item_registry = ItemRegistry()
        self.scene_registry = SceneRegistry()

        # Feature-gated registries — None when feature flag off
        self.species_registry = None
        self.backgrounds: dict = {}
        self.npc_registry = None
        self.quest_registry = None
        self.guild_registry = None
        self.faction_registry = None

        # World content
        self.system_messages: dict = {}
        self.lore_data: dict = {}

        # Runtime state
        self.state: GameState | None = None
        self.ai_service = None       # AIService facade — systems' boundary to AI
        self._bg_generator = None
        self._world_director = None
        self._bot_manager = None
        self._running = False

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def bootstrap(self) -> None:
        self._load_data()
        self._setup_ai()
        self._setup_notifications()

    def _load_data(self) -> None:
        """Delegate — see core/bootstrap.load_registries."""
        from core.bootstrap import load_registries
        load_registries(self)

    def _setup_ai(self) -> None:
        """Delegate — see core/bootstrap.setup_ai."""
        from core.bootstrap import setup_ai
        setup_ai(self)

    def _ai_online(self) -> bool:
        """True when AI calls can be made. All AI goes through ``self.ai_service``."""
        return self.ai_service is not None and self.ai_service.is_available

    def _setup_notifications(self) -> None:
        setup_notification_listeners(renderer.console, self.system_messages)

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self.bootstrap()
        renderer.clear()
        renderer.print_title()
        self._main_menu()

    # ── Menu / loop delegates ─────────────────────────────────────────────────

    def _main_menu(self) -> None:
        from core.menu_flow import main_menu
        main_menu(self)

    def _load_game_menu(self, saves: list[str]) -> None:
        from core.menu_flow import load_game_menu
        load_game_menu(self, saves)

    def _new_game(self) -> None:
        from core.menu_flow import new_game
        new_game(self)

    def _game_loop(self) -> None:
        from core.game_loop import run_game_loop
        run_game_loop(self)

    # ── Per-turn delegates ────────────────────────────────────────────────────

    def _render_scene(self) -> None:
        from ui.scene_renderer import render_scene
        render_scene(self)

    def _get_current_options(self) -> list[SceneOption]:
        from core.input_handler import get_current_options
        return get_current_options(self)

    def _prompt_choice(self, options: list[SceneOption]) -> SceneOption | None:
        from core.input_handler import prompt_choice
        return prompt_choice(self, options)

    def _integrate_background_content(self) -> None:
        from core.background_integrator import integrate_results
        integrate_results(self)

    def _maybe_submit_background_task(self) -> None:
        from core.bg_scheduler import maybe_submit_tasks
        maybe_submit_tasks(self)

    # ── Player-facing handlers ────────────────────────────────────────────────

    def _handle_situation_query(self, current_options: list) -> None:
        from core.situation_query import handle_situation_query
        handle_situation_query(self, current_options)

    def _handle_rest(self, rest_type: str = "short") -> None:
        from systems.rest_system import handle_rest
        handle_rest(self, rest_type)

    def _show_auction_house(self) -> None:
        from ui.auction_ui import show_auction_house
        show_auction_house(self)

    # ── Faction endings ──────────────────────────────────────────────────────

    def _update_faction_standing(self, faction_id: str, delta: float) -> None:
        """Handle the ``update_faction:faction_id:delta`` choice trigger."""
        from systems.faction_endings import update_faction_standing
        update_faction_standing(self, faction_id, delta)

    def _check_ending_paths(self) -> None:
        from systems.faction_endings import check_ending_paths
        check_ending_paths(self)
