from __future__ import annotations

import logging

from core.event_bus import bus

logger = logging.getLogger(__name__)
from core.state_manager import GameState
from entities.character_class import ClassRegistry
from entities.skill import SkillRegistry
from entities.item import ItemRegistry
from scenes.scene_registry import SceneRegistry
from scenes.scene_base import SceneOption
from ui import renderer
from config import feature
from core.menus import GameMenusMixin
from core.combat_handler import CombatHandlerMixin
from core.dialogue_handler import DialogueHandlerMixin
from core.choice_handler import ChoiceHandlerMixin


class GameEngine(GameMenusMixin, CombatHandlerMixin, DialogueHandlerMixin, ChoiceHandlerMixin):
    def __init__(self) -> None:
        self.class_registry = ClassRegistry()
        self.skill_registry = SkillRegistry()
        self.item_registry = ItemRegistry()
        self.scene_registry = SceneRegistry()
        self.species_registry = None   # loaded if species_system feature enabled
        self.backgrounds: dict = {}    # loaded if species_system feature enabled
        self.npc_registry = None       # loaded if npc_system feature enabled
        self.quest_registry = None     # loaded if quest_system feature enabled
        self.guild_registry = None     # loaded if guild_system feature enabled
        self.faction_registry = None   # loaded if faction_system feature enabled
        self.system_messages: dict = {}
        self.lore_data: dict = {}
        self.state: GameState | None = None
        self.ai_generator = None
        self.ai_service = None  # AIService facade — the boundary systems depend on
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
        """Thin delegate — real logic lives in core/bootstrap.load_registries."""
        from core.bootstrap import load_registries
        load_registries(self)

    def _setup_ai(self) -> None:
        """Thin delegate — real logic lives in core/bootstrap.setup_ai."""
        from core.bootstrap import setup_ai
        setup_ai(self)

    def _setup_notifications(self) -> None:
        setup_notification_listeners(renderer.console, self.system_messages)

    # ── Main Menu ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.bootstrap()
        renderer.clear()
        renderer.print_title()
        self._main_menu()

    def _main_menu(self) -> None:
        """Thin delegate — real logic lives in core/menu_flow.py."""
        from core.menu_flow import main_menu
        main_menu(self)

    def _load_game_menu(self, saves: list[str]) -> None:
        """Thin delegate — real logic lives in core/menu_flow.py."""
        from core.menu_flow import load_game_menu
        load_game_menu(self, saves)

    def _new_game(self) -> None:
        """Thin delegate — real logic lives in core/menu_flow.py."""
        from core.menu_flow import new_game
        new_game(self)

    # ── Game Loop ──────────────────────────────────────────────────────────────

    def _game_loop(self) -> None:
        """Thin delegate — real logic lives in core/game_loop.py."""
        from core.game_loop import run_game_loop
        run_game_loop(self)

    def _render_scene(self) -> None:
        renderer.clear()
        renderer.print_title()

        scene = self.scene_registry.get(self.state.current_scene_id)
        if not scene:
            renderer.print_error(f"Scene not found: {self.state.current_scene_id}")
            return

        # Show status bar at top for non-prologue scenes
        if self.state.current_scene_id not in ("prologue", "character_creation"):
            renderer.print_status_bar(self.state.player, self.item_registry)
            renderer.print_divider()

        renderer.print_scene_header(scene.title)

        # Show entrance text only the first time this scene's root is visited
        node_id = self.state.current_node_id
        scene_key = self.state.current_scene_id
        if node_id == "root" and scene.entrance_text and scene_key not in self.state._entrance_shown:
            self.state._entrance_shown.add(scene_key)
            renderer.print_scene_text(scene.entrance_text, pause_between=0.05)
            renderer.print_divider()

        # Show current node text
        node = scene.get_node(node_id)
        node_text = node.get("text", "")
        if node_text:
            # Handle status_check node specially
            if node_id == "status_check":
                renderer.print_full_status(self.state.player, self.item_registry, self.skill_registry)
            else:
                renderer.print_scene_text([node_text])

        # Show bots present in this zone (if bot_system active)
        if feature("bot_system") and self._bot_manager:
            bots_here = [
                b for b in self._bot_manager.all()
                if b.current_zone_id == self.state.current_scene_id
            ]
            if bots_here:
                names = ", ".join(b.name for b in bots_here)
                renderer.console.print(
                    f"  [dim_text]Also here: {names}[/dim_text]"
                )

    def _get_current_options(self) -> list[SceneOption]:
        """Thin delegate — real logic lives in core/input_handler.py."""
        from core.input_handler import get_current_options
        return get_current_options(self)

    def _prompt_choice(self, options: list[SceneOption]) -> SceneOption | None:
        """Thin delegate — real logic lives in core/input_handler.py."""
        from core.input_handler import prompt_choice
        return prompt_choice(self, options)

    def _update_faction_standing(self, faction_id: str, delta: float) -> None:
        """Handle update_faction:faction_id:delta trigger."""
        if not self.faction_registry:
            return
        from systems import faction_system
        faction_system.update_standing(faction_id, delta, self.state, self.faction_registry)
        bus.flush()

    def _check_ending_paths(self) -> None:
        """
        Check if the player qualifies for either ending path.
        Notifies once by setting a flag so the message only fires once.
        """
        if self.state.player.has_flag("ending_path_notified"):
            return
        from systems import faction_system
        path = faction_system.check_political_path(self.state, self.faction_registry)
        if path:
            self.state.player.set_flag("ending_path_notified")
            self.state.player.set_flag(path)  # 'rule_the_system' or 'destroy_the_system'
            if path == "rule_the_system":
                renderer.print_system_message(
                    "THE SYSTEM ACKNOWLEDGES YOUR ASCENT. A path opens before you.",
                    style="system_msg",
                )
            else:
                renderer.print_system_message(
                    "THE SIGNAL IS READY. The Fracture Core awaits.",
                    style="system_warning",
                )
            bus.flush()

    def _show_auction_house(self) -> None:
        """Thin delegate — real logic lives in ui/auction_ui.py."""
        from ui.auction_ui import show_auction_house
        show_auction_house(self)

    def _handle_situation_query(self, current_options: list) -> None:
        """Thin delegate — real logic lives in core/situation_query.py."""
        from core.situation_query import handle_situation_query
        handle_situation_query(self, current_options)

    def _handle_rest(self, rest_type: str = "short") -> None:
        """Handle camp rest. full=8hrs (100% HP/MP), short=2hrs (40% HP/MP). 10% ambush chance for camp."""
        import random
        from systems.buff_system import tick_buffs

        player = self.state.player

        if rest_type == "full":
            hp_pct, mp_pct, tick_count, label = 1.0, 1.0, 8, "Full Rest (8 hours)"
        else:
            hp_pct, mp_pct, tick_count, label = 0.4, 0.4, 2, "Light Rest (2 hours)"

        healed_hp = int(player.max_hp * hp_pct)
        healed_mp = int(player.max_mp * mp_pct)
        player.current_hp = min(player.max_hp, player.current_hp + healed_hp)
        player.current_mp = min(player.max_mp, player.current_mp + healed_mp)

        for _ in range(tick_count):
            tick_buffs(player)

        renderer.print_divider()
        renderer.print_system_message(
            f"{label}: +{healed_hp} HP, +{healed_mp} MP restored.",
            style="success",
        )

        # 10% ambush chance on camp rest
        if random.random() < 0.10:
            renderer.print_system_message(
                "Something stirs in the dark. You are not alone.", style="system_warning"
            )
            renderer.prompt_any_key()
            self._run_combat("goblin_scout")
        else:
            renderer.prompt_any_key()

    def _integrate_background_content(self) -> None:
        """Thin delegate — real logic lives in core/background_integrator.py."""
        from core.background_integrator import integrate_results
        integrate_results(self)

    def _maybe_submit_background_task(self) -> None:
        """Thin delegate — real logic lives in core/bg_scheduler.py."""
        from core.bg_scheduler import maybe_submit_tasks
        maybe_submit_tasks(self)

