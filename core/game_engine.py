from __future__ import annotations

import logging
import time

import questionary

from core.event_bus import bus

logger = logging.getLogger(__name__)
from core.state_manager import GameState
from entities.player import Player, Stats
from entities.character_class import ClassRegistry
from entities.skill import SkillRegistry
from entities.item import ItemRegistry
from scenes.scene_registry import SceneRegistry
from scenes.scene_base import SceneOption
from systems import class_system, level_system, combat_system, inventory_system
from persistence.save_manager import save_game, load_game, list_saves, new_game_state, close_game
from ui import renderer
from config import SAVES_DIR, DATA_DIR, feature, GUILD_SIM_INTERVAL
from entities.enums import SkillType
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
        saves = list_saves(SAVES_DIR)
        choices = ["New Game"]
        if saves:
            choices.append("Load Game")
        choices.append("Quit")

        choice = questionary.select("", choices=choices).ask()

        if choice == "New Game":
            self._new_game()
        elif choice == "Load Game":
            self._load_game_menu(saves)
        else:
            renderer.console.print("\n  [dim_text]Farewell.[/dim_text]\n")

    def _load_game_menu(self, saves: list[str]) -> None:
        choice = questionary.select("Select save:", choices=saves + ["← Back"]).ask()
        if choice == "← Back":
            self._main_menu()
            return
        state = load_game(choice, SAVES_DIR)
        if state:
            self.state = state
            self.state.skill_registry = self.skill_registry
            # Re-register any AI-generated skills stored in the world DB
            for defn in getattr(self.state, "_ai_skill_defs", []):
                from entities.skill import Skill
                skill = Skill.model_validate(defn)
                self.skill_registry.register(skill)
            self.state._ai_skill_defs = []
            renderer.print_success(f"Loaded save: {choice}")
            self._game_loop()
        else:
            renderer.print_error("Failed to load save.")
            self._main_menu()

    # ── New Game ───────────────────────────────────────────────────────────────

    def _new_game(self) -> None:
        renderer.clear()
        renderer.print_system_message("WHAT IS YOUR NAME, ADVENTURER?")
        name = questionary.text("Name:").ask()
        if not name or not name.strip():
            name = "Wanderer"
        player = Player(name=name.strip())
        import re
        slot_name = re.sub(r'[^a-z0-9_]', '', player.name.lower().replace(" ", "_")) or "save"
        self.state = new_game_state(player, SAVES_DIR, slot_name)
        self.state.skill_registry = self.skill_registry
        self.state.current_scene_id = "prologue"
        self.state.current_node_id = "root"
        renderer.print_success(f"Welcome, {player.name}.")
        time.sleep(0.5)
        self._game_loop()

    # ── Game Loop ──────────────────────────────────────────────────────────────

    def _game_loop(self) -> None:
        from utils.logging_setup import log_player_error
        self._running = True
        try:
            while self._running and self.state:
                bus.flush()
                # Poll and integrate background AI content
                self._integrate_background_content()
                self._maybe_submit_background_task()
                if self._world_director and self.state:
                    self._world_director.tick(self.state, self.state.player.turn_count)
                self.state.advance_turn()

                # Buff tick — decrement buff durations
                if self.state.player.active_buffs:
                    from systems.buff_system import tick_buffs
                    expired = tick_buffs(self.state.player)

                # Skill cooldown tick
                if self.state.player.skill_cooldowns:
                    from systems.skill_system import tick_skill_cooldowns
                    tick_skill_cooldowns(self.state.player)
                    # Expired buffs are silently removed (no notification needed for normal buffs)

                # Alignment inertia — nudge toward 0 every N turns
                if feature("alignment_system"):
                    from systems.alignment_system import should_apply_inertia, apply_inertia
                    if should_apply_inertia(self.state.player.turn_count):
                        apply_inertia(self.state.player)

                # Quest tick — check silent completions and failures
                if feature("quest_system") and self.quest_registry:
                    from systems import quest_system
                    quest_system.tick_quests(self.state, self.quest_registry)
                    bus.flush()

                # Auction tick — expire listings, NPC counter-bids
                if feature("auction_house"):
                    from systems import auction_system
                    auction_system.tick_auction(self.state)

                # Check for ending path unlock (political ascension)
                if feature("faction_system") and self.faction_registry:
                    self._check_ending_paths()

                # Autonomous faction relation drift
                if feature("faction_system") and feature("world_db") and self.state.world_db:
                    from systems.faction_system import drift_faction_relations
                    drift_changes = drift_faction_relations(
                        self.state.world_db, self.state.player.turn_count
                    )
                    for fa, fb, delta in drift_changes:
                        direction = "warmer" if delta > 0 else "cooler"
                        self.state.world_db.store_world_event(
                            event_type="rumor",
                            event_text=(
                                f"Relations between {fa.replace('_', ' ').title()} and "
                                f"{fb.replace('_', ' ').title()} grow {direction}."
                            ),
                            zone_id="verath_city",
                            title="Political Shift",
                            npc_hint="",
                            generated_turn=self.state.player.turn_count,
                        )

                self._render_scene()
                options = self._get_current_options()
                if not options:
                    renderer.print_error("No options available. Returning to main menu.")
                    break
                choice = self._prompt_choice(options)
                if choice is None:
                    continue
                try:
                    self._handle_choice(choice)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    log_player_error(
                        "choice_crash",
                        exc=exc,
                        player=self.state.player,
                        scene_id=self.state.current_scene_id,
                        node_id=self.state.current_node_id,
                        option_id=choice.option_id,
                        turn=self.state.player.turn_count,
                    )
                    logger.error("Unhandled error in _handle_choice: %s", exc, exc_info=True)
                    from ui import renderer
                    renderer.print_error("Something went wrong. The System logged the anomaly.")
        finally:
            # Always close the world DB cleanly on exit, crash, or KeyboardInterrupt
            if self.state:
                close_game(self.state)
            if self._bg_generator:
                self._bg_generator.stop()
            if self.ai_service is not None:
                self.ai_service.shutdown()

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
        """
        Handle player's natural-language question about the current situation.
        AI generates new situational options injected into the current node.
        """
        if not self.ai_generator:
            renderer.print_system_message(
                "The System is silent. (AI offline — enable Ollama to use this feature.)",
                style="dim_text",
            )
            renderer.prompt_any_key()
            return

        renderer.console.print()
        question = questionary.text(
            "What do you want to know or try?",
            instruction="(e.g. 'Can I aim for the head?' or 'Is there a way to sneak past?')"
        ).ask()
        if not question or not question.strip():
            return

        question = question.strip()
        logger.info(
            "Dynamic query: scene=%s node=%s question=%r",
            self.state.current_scene_id, self.state.current_node_id, question,
        )

        # Get current scene context
        scene = self.scene_registry.get(self.state.current_scene_id)
        scene_title = scene.title if scene else self.state.current_scene_id
        node = scene.get_node(self.state.current_node_id) if scene else {}
        scene_text = node.get("text", "")
        if not scene_text and scene and scene.entrance_text:
            scene_text = " ".join(scene.entrance_text[:2])

        option_labels = [opt.label for opt in current_options if not opt.locked]

        # Call AI with spinner
        result = None
        try:
            with renderer.show_ai_thinking_spinner(f"ANALYZING: {question[:40]}..."):
                result = self.ai_generator.generate_dynamic_options(
                    question=question,
                    scene_title=scene_title,
                    scene_text=scene_text,
                    current_options=option_labels,
                    player=self.state.player,
                )
        except Exception as exc:
            from utils.logging_setup import log_player_error
            log_player_error(
                "dynamic_query_crash",
                exc=exc,
                player=self.state.player,
                scene_id=self.state.current_scene_id,
                node_id=self.state.current_node_id,
                turn=self.state.player.turn_count,
                extra={"question": question[:120]},
            )
            logger.error("Dynamic query exception: %s", exc, exc_info=True)

        if not result:
            logger.warning(
                "Dynamic query returned no result: scene=%s question=%r",
                self.state.current_scene_id, question,
            )
            renderer.print_system_message(
                "The System could not generate a response. Try rephrasing your question.",
                style="dim_text",
            )
            renderer.prompt_any_key()
            return

        # Show situation narrative
        renderer.print_divider()
        renderer.print_scene_text([result.situation_text])
        renderer.print_divider()

        if not result.options:
            renderer.print_system_message("No new options could be generated for that question.", style="dim_text")
            renderer.prompt_any_key()
            return

        # Convert AI options to SceneOption objects
        from scenes.scene_base import SceneOption

        new_scene_options = []
        for ai_opt in result.options:
            # Build requires checks
            requires = ai_opt.requires or {}
            locked = False
            lock_reason = ""

            min_stats = requires.get("min_stats", {})
            for stat, val in min_stats.items():
                if getattr(self.state.player.stats, stat, 0) < val:
                    locked = True
                    lock_reason = f"Requires {stat} >= {val}"
                    break

            req_flags = requires.get("flags", [])
            if not locked:
                for flag in req_flags:
                    if not self.state.player.has_flag(flag):
                        locked = True
                        lock_reason = "Condition not met"
                        break

            scene_opt = SceneOption(
                option_id=ai_opt.option_id,
                label=f"[AI] {ai_opt.label}",
                leads_to="__stay__",
                leads_to_node=self.state.current_node_id,
                expected=False,  # always a divergence signal
                triggers=ai_opt.triggers,
                locked=locked,
                lock_reason=lock_reason,
                narrative=ai_opt.narrative,  # shown as feedback when chosen
            )
            new_scene_options.append(scene_opt)

        # Store in state so they appear in the next render
        state_key = f"{self.state.current_scene_id}:{self.state.current_node_id}"
        self.state._dynamic_options[state_key] = new_scene_options

        renderer.console.print("  [dim_text]New options unlocked. Choose below.[/dim_text]")
        renderer.prompt_any_key()

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

