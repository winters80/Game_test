from __future__ import annotations

import logging

import questionary

from core.event_bus import bus

logger = logging.getLogger(__name__)
from core.state_manager import GameState
from entities.character_class import ClassRegistry
from entities.skill import SkillRegistry
from entities.item import ItemRegistry
from scenes.scene_registry import SceneRegistry
from scenes.scene_base import SceneOption
from ui import renderer
from config import feature, GUILD_SIM_INTERVAL
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
        scene = self.scene_registry.get(self.state.current_scene_id)
        if not scene:
            return []
        options = scene.get_options(self.state, self.state.current_node_id)
        # Inject any AI-generated dynamic options for this node
        state_key = f"{self.state.current_scene_id}:{self.state.current_node_id}"
        dynamic = self.state._dynamic_options.get(state_key, [])
        if dynamic:
            options = list(options) + dynamic
        return options

    def _prompt_choice(self, options: list[SceneOption]) -> SceneOption | None:
        renderer.print_options(options)

        # Build questionary choices (skip locked ones)
        available = [(i + 1, opt) for i, opt in enumerate(options) if not opt.locked]
        if not available:
            renderer.print_error("All options are locked.")
            return None

        extra = ["[?] Ask about this situation", "[K] Skills", "[I] Items & Equipment", "[L] World Log", "[S] Save game", "[A] Admin Panel", "[Q] Quit to menu"]
        if feature("crafting_system"):
            player = self.state.player
            if player.has_flag("alchemist") or player.has_flag("crafter"):
                extra.insert(2, "[C] Craft")
        if feature("guild_system"):
            extra.insert(-1, "[G] Found a Guild")
        if feature("quest_system") and self.quest_registry:
            extra.insert(3, "[J] Quest Journal")
        choice_labels = [f"{i}. {opt.label}" for i, opt in available] + extra
        answer = questionary.select("Choose:", choices=choice_labels).ask()

        if answer is None or answer.startswith("[Q]"):
            self._running = False
            return None
        if answer.startswith("[S]"):
            self._save_prompt()
            return None
        if answer.startswith("[A]"):
            self._admin_panel()
            return None
        if answer.startswith("[?]"):
            self._handle_situation_query(options)
            return None
        if answer.startswith("[K]"):
            self._skills_menu()
            return None
        if answer.startswith("[I]"):
            self._inventory_menu()
            return None
        if answer.startswith("[C]"):
            self._craft_menu()
            return None
        if answer.startswith("[L]"):
            self._lore_log()
            return None
        if answer.startswith("[G]"):
            self._found_guild_menu()
            return None
        if answer.startswith("[J]"):
            self._quests_menu()
            return None

        # Parse number
        try:
            num = int(answer.split(".")[0])
            for i, opt in available:
                if i == num:
                    return opt
        except (ValueError, IndexError):
            pass
        return None

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
        """Interactive auction house UI."""
        from systems import auction_system
        auction_system.seed_initial_listings(self.state, self.guild_registry)

        while True:
            listings = auction_system.get_listings_display(self.state)
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ AUCTION HOUSE ][/system_msg]\n")

            if not listings:
                renderer.console.print("  [dim_text]No active listings.[/dim_text]\n")
            else:
                renderer.console.print(f"  {'Item':<25} {'Bid':>8} {'Turns':>6}  {'Guild'}")
                renderer.console.print("  " + "-" * 55)
                for lst in listings:
                    renderer.console.print(
                        f"  [{lst['listing_id'][:6]}] {lst['item']:<20} {lst['current_bid']:>8,} Shards  "
                        f"{lst['turns_left']:>4} turns  {lst['guild']}"
                    )

            price = auction_system.get_life_token_price(self.state)
            renderer.console.print(f"\n  Life token market price: [gold]{price:,} Shards[/gold]")
            renderer.console.print(f"  Your Shards: [gold]{self.state.player.gold:,}[/gold]")

            choices = ["Buy life token (direct)", "Bid on listing", "Leave auction house"]
            action = questionary.select("Auction house:", choices=choices).ask()

            if action is None or action == "Leave auction house":
                break
            elif action == "Buy life token (direct)":
                ok, msg = auction_system.buy_life_token_direct(self.state)
                renderer.print_system_message(msg, style="system_msg" if ok else "system_warning")
                bus.flush()
                renderer.prompt_any_key()
            elif action == "Bid on listing" and listings:
                listing_ids = [f"{l['listing_id'][:6]} — {l['item']} (current: {l['current_bid']:,})" for l in listings]
                listing_ids.append("← Back")
                selected = questionary.select("Select listing:", choices=listing_ids).ask()
                if selected and selected != "← Back":
                    idx = listing_ids.index(selected)
                    chosen = listings[idx]
                    min_bid = chosen["current_bid"] + 1
                    bid_str = questionary.text(f"Enter bid amount (min {min_bid:,}):").ask()
                    try:
                        bid_amount = int(bid_str or "0")
                        ok, msg = auction_system.place_player_bid(chosen["listing_id"], bid_amount, self.state)
                        renderer.print_system_message(msg, style="system_msg" if ok else "system_warning")
                        bus.flush()
                    except ValueError:
                        renderer.print_error("Invalid bid amount.")
                    renderer.prompt_any_key()

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
        """Submit background generation tasks at configured intervals."""
        if not self._bg_generator or not self.state:
            return

        # Always update autonomous context so worker thread has current state
        zone_id   = self.state.current_scene_id
        scene     = self.scene_registry.get(zone_id)
        zone_name = scene.title if scene else zone_id.replace("_", " ").title()
        context_flags = [k for k in self.state.player.flags if not k.startswith("_")][:10]
        self._bg_generator.update_context(
            zone_id=zone_id,
            zone_name=zone_name,
            player_level=self.state.player.level,
            flags=context_flags,
            turn=self.state.player.turn_count,
        )

        from config import BG_GEN_INTERVAL

        player = self.state.player
        turn = player.turn_count

        # Fire at turn 1 (first real turn) and every BG_GEN_INTERVAL turns after
        if turn < 1 or (turn > 1 and turn % BG_GEN_INTERVAL != 0):
            return

        zone_id       = self.state.current_scene_id
        scene         = self.scene_registry.get(zone_id)
        zone_name     = scene.title if scene else zone_id
        context_flags = [k for k in player.flags if not k.startswith("_")][:8]
        player_level  = player.level

        # ── Always: world event for the current zone ──────────────────────────
        self._bg_generator.submit_world_event(
            zone_id=zone_id,
            zone_name=zone_name,
            player_level=player_level,
            context_flags=context_flags,
        )

        # ── Rotate through deeper content types ───────────────────────────────
        rotation = (turn // max(BG_GEN_INTERVAL, 1)) % 5

        if rotation == 0:
            # AI quest tailored to player's current situation
            self._bg_generator.submit_quest(
                zone_id=zone_id,
                npc_hint="a contact in the area",
                player=player,
            )
        elif rotation == 1:
            # Refresh zone entrance narrative
            bg_narrative_flag = f"_bg_narrative_submitted:{zone_id}"
            if not player.has_flag(bg_narrative_flag):
                player.set_flag(bg_narrative_flag)
            self._bg_generator.submit_zone_narrative(zone_id, zone_name, context_flags)
        elif rotation == 2:
            # Local rumour
            self._bg_generator.submit_rumor(
                zone_id=zone_id,
                context_flags=context_flags,
                player_level=player_level,
            )
        elif rotation == 3:
            # Lore entry
            self._bg_generator.submit_lore_entry(
                context_flags=context_flags,
                player_flags=context_flags,
            )
        else:
            # Area activity texture
            self._bg_generator.submit_area_activity(
                zone_id=zone_id,
                zone_name=zone_name,
                context_flags=context_flags,
            )

        # ── NPC branch enrichment ─────────────────────────────────────────────
        if turn % (BG_GEN_INTERVAL * 3) == 0 and feature("npc_system") and self.npc_registry:
            player_profile = {
                "level": player_level,
                "alignment": player.alignment,
                "active_class": player.active_class or player.base_class or "Unclassified",
            }
            # Pick a visible NPC to enrich
            for npc_id in ("torven_blacksmith", "mira_innkeeper", "sylara_guildmaster"):
                npc = self.npc_registry.get(npc_id)
                if npc:
                    self._bg_generator.submit_npc_branch(npc_id, npc.name, player_profile)
                    break

        # ── Guild simulation tick ─────────────────────────────────────────────
        if feature("guild_system") and self.guild_registry and turn % GUILD_SIM_INTERVAL == 0:
            self._bg_generator.submit_guild_tick(
                world_db=self.state.world_db,
                guild_registry=self.guild_registry,
            )

        # ── Bot decisions ─────────────────────────────────────────────────────
        if feature("bot_system") and self._bot_manager:
            world_context = {"player_zone": zone_id, "turn": turn}
            for bot in self._bot_manager.all():
                bot_profile = {
                    "name": bot.name,
                    "personality_seed": bot.personality_seed,
                    "current_goal": bot.current_goal,
                    "current_zone_id": bot.current_zone_id,
                }
                self._bg_generator.submit_bot_decision(bot.bot_id, bot_profile, world_context)

