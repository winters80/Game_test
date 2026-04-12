from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import questionary

from core.event_bus import bus
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
from ui.notifications import setup_notification_listeners
from config import SAVES_DIR, DATA_DIR, AI_ENABLED, OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_MAX_RETRIES, feature

if TYPE_CHECKING:
    pass


class GameEngine:
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
        self._bg_generator = None
        self._running = False

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def bootstrap(self) -> None:
        self._load_data()
        self._setup_ai()
        self._setup_notifications()

    def _load_data(self) -> None:
        self.class_registry.load_from_file(DATA_DIR / "classes" / "base_classes.json")
        self.class_registry.load_from_file(DATA_DIR / "classes" / "combo_classes.json")
        self.skill_registry.load_from_dir(DATA_DIR / "skills")
        self.item_registry.load_from_dir(DATA_DIR / "items")
        self.scene_registry.load_from_dir(Path(__file__).parent.parent / "scenes" / "data")

        lore_path = DATA_DIR / "world" / "lore_fragments.json"
        if lore_path.exists():
            self.lore_data = json.loads(lore_path.read_text(encoding="utf-8"))

        msg_path = DATA_DIR / "world" / "system_messages.json"
        if msg_path.exists():
            self.system_messages = json.loads(msg_path.read_text(encoding="utf-8"))

        if feature("species_system"):
            from entities.species import SpeciesRegistry
            from systems.species_system import load_backgrounds
            self.species_registry = SpeciesRegistry()
            self.species_registry.load_from_file(DATA_DIR / "species" / "species_definitions.json")
            self.backgrounds = load_backgrounds(DATA_DIR)

        if feature("npc_system"):
            from entities.npc import NPCRegistry
            self.npc_registry = NPCRegistry()
            self.npc_registry.load_from_file(DATA_DIR / "npcs" / "npcs.json")

        if feature("quest_system"):
            from entities.quest import QuestRegistry
            self.quest_registry = QuestRegistry()
            self.quest_registry.load_from_file(DATA_DIR / "quests" / "quest_templates.json")

        if feature("guild_system"):
            from entities.guild import GuildRegistry
            self.guild_registry = GuildRegistry()
            self.guild_registry.load_from_file(DATA_DIR / "guilds" / "guild_definitions.json")

        if feature("faction_system"):
            from entities.faction import FactionRegistry
            self.faction_registry = FactionRegistry()
            self.faction_registry.load_from_file(DATA_DIR / "factions" / "faction_definitions.json")

    def _setup_ai(self) -> None:
        if not AI_ENABLED:
            return
        try:
            from ai.ollama_client import OllamaClient
            from ai.content_generator import ContentGenerator
            client = OllamaClient(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
            if client.is_available():
                self.ai_generator = ContentGenerator(client, self.lore_data, OLLAMA_MODEL)
                if feature("world_db"):
                    from config import BG_GEN_ENABLED
                    if BG_GEN_ENABLED:
                        from ai.background_generator import BackgroundGenerator
                        self._bg_generator = BackgroundGenerator(self.ai_generator)
                        self._bg_generator.start()
                renderer.print_success("AI system online. Ollama connected.")
            else:
                renderer.console.print("  [dim_text]Ollama not available — AI features disabled.[/dim_text]")
        except Exception as e:
            renderer.console.print(f"  [dim_text]AI setup failed: {e} — continuing without AI.[/dim_text]")

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
        slot_name = player.name.lower().replace(" ", "_")
        self.state = new_game_state(player, SAVES_DIR, slot_name)
        self.state.current_scene_id = "prologue"
        self.state.current_node_id = "root"
        renderer.print_success(f"Welcome, {player.name}.")
        time.sleep(0.5)
        self._game_loop()

    # ── Game Loop ──────────────────────────────────────────────────────────────

    def _game_loop(self) -> None:
        self._running = True
        try:
            while self._running and self.state:
                bus.flush()
                # Poll and integrate background AI content
                self._integrate_background_content()
                self._maybe_submit_background_task()
                self.state.advance_turn()

                # Buff tick — decrement buff durations
                if self.state.player.active_buffs:
                    from systems.buff_system import tick_buffs
                    expired = tick_buffs(self.state.player)
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

                self._render_scene()
                options = self._get_current_options()
                if not options:
                    renderer.print_error("No options available. Returning to main menu.")
                    break
                choice = self._prompt_choice(options)
                if choice is None:
                    continue
                self._handle_choice(choice)
        finally:
            # Always close the world DB cleanly on exit, crash, or KeyboardInterrupt
            if self.state:
                close_game(self.state)
            if self._bg_generator:
                self._bg_generator.stop()

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

        extra = ["[?] Ask about this situation", "[I] Items & Equipment", "[S] Save game", "[A] Admin Panel", "[Q] Quit to menu"]
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
        if answer.startswith("[I]"):
            self._inventory_menu()
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

    def _handle_choice(self, option: SceneOption) -> None:
        player = self.state.player
        scene = self.scene_registry.get(self.state.current_scene_id)

        # Record as unexpected if flagged
        if not option.expected:
            key = f"unexpected:{self.state.current_scene_id}:{option.option_id}"
            player.record_choice(key)

        # Process triggers
        if scene:
            scene.process_triggers(option.triggers, self.state)

        # Handle special triggers that need engine context
        for trigger in option.triggers:
            if trigger.startswith("set_base_class:"):
                class_id = trigger[15:]
                self._assign_class(class_id)
            elif trigger.startswith("combat:"):
                encounter_id = trigger[7:]
                self._run_combat(encounter_id)
            elif trigger.startswith("talk_npc:"):
                template_id = trigger[9:]
                self._run_dialogue(template_id)
            elif trigger.startswith("join_guild:") and feature("guild_system") and self.guild_registry:
                guild_id = trigger[11:]
                self._join_guild(guild_id)
            elif trigger.startswith("update_faction:") and feature("faction_system") and self.faction_registry:
                # format: update_faction:faction_id:+25.0
                parts = trigger.split(":")
                if len(parts) == 3:
                    self._update_faction_standing(parts[1], float(parts[2]))
            elif trigger == "buy_life_token" and feature("auction_house"):
                from systems import auction_system
                ok, msg = auction_system.buy_life_token_direct(self.state)
                renderer.print_system_message(msg, style="system_msg" if ok else "system_warning")
                renderer.prompt_any_key()
            elif trigger == "show_auction" and feature("auction_house"):
                self._show_auction_house()
            elif trigger.startswith("start_quest:") and feature("quest_system") and self.quest_registry:
                quest_id = trigger[12:]
                from systems import quest_system as qs
                qs.start_quest(quest_id, None, self.state, self.quest_registry)
                bus.flush()

        # Apply pending species (set by scene_base as _pending_species:<id>)
        if feature("species_system") and self.species_registry:
            self._apply_pending_species(player)
            self._apply_pending_background(player)

        # Refresh item names in notifications now that we may have them
        bus.flush()

        # Transition
        if option.leads_to and option.leads_to != "__stay__":
            self.state.current_scene_id = option.leads_to
            self.state.current_node_id = option.leads_to_node or "root"
        else:
            self.state.current_node_id = option.leads_to_node or "root"

        # Clear dynamic options when navigating to a new node
        if option.leads_to and option.leads_to != "__stay__":
            new_key = f"{self.state.current_scene_id}:{self.state.current_node_id}"
            self.state._dynamic_options.pop(new_key, None)

        self.state.mark_dirty()

    def _apply_pending_species(self, player) -> None:
        """Apply _pending_species:<id> flag set by scene triggers."""
        from systems.species_system import apply_species
        prefix = "_pending_species:"
        pending = [f for f in list(player.flags) if f.startswith(prefix)]
        for flag in pending:
            species_id = flag[len(prefix):]
            species = self.species_registry.get(species_id)
            if species:
                apply_species(player, species)
            del player.flags[flag]

    def _apply_pending_background(self, player) -> None:
        """Apply _pending_background:<id> flag set by scene triggers."""
        from systems.species_system import apply_background
        prefix = "_pending_background:"
        pending = [f for f in list(player.flags) if f.startswith(prefix)]
        for flag in pending:
            bg_id = flag[len(prefix):]
            bg_data = self.backgrounds.get(bg_id)
            if bg_data:
                apply_background(player, bg_data, self.item_registry)
            del player.flags[flag]

    def _assign_class(self, class_id: str) -> None:
        ok, msg = class_system.assign_base_class(
            self.state.player, class_id, self.class_registry, self.skill_registry
        )
        if ok:
            cls = self.class_registry.get(class_id)
            if cls:
                renderer.print_class_reveal(cls)
                renderer.prompt_any_key()

    def _run_combat(self, encounter_id: str) -> None:
        enemies = combat_system.spawn_encounter(encounter_id)
        if not enemies:
            return

        renderer.print_divider()
        renderer.print_system_message("COMBAT INITIATED", style="system_warning")
        time.sleep(0.5)

        player = self.state.player
        turn = 0
        alive_enemies = [e for e in enemies if e.is_alive]

        while alive_enemies and player.current_hp > 0:
            turn += 1
            renderer.clear()
            renderer.print_title()
            renderer.print_status_bar(player, self.item_registry)
            renderer.print_divider()

            # Show enemies
            for enemy in alive_enemies:
                renderer.print_combat_header(enemy.name, enemy.current_hp, enemy.max_hp)

            # Player turn
            skills_available = [sid for sid in player.skills if self.skill_registry.get(sid)]
            combat_choices = ["Basic Attack"] + [
                self.skill_registry.get(sid).name
                for sid in skills_available
                if self.skill_registry.get(sid)
            ] + ["Flee"]

            action = questionary.select("Your action:", choices=combat_choices).ask()
            target = alive_enemies[0]

            if action == "Flee":
                if combat_system.try_flee(player, target):
                    renderer.print_scene_text(["You flee from combat!"])
                    renderer.prompt_any_key()
                    return
                else:
                    renderer.print_scene_text(["You failed to flee!"])
            elif action == "Basic Attack":
                dmg, is_crit = combat_system.player_attack(player, target)
                style = "critical" if is_crit else "damage"
                renderer.print_combat_action(
                    "You", "strike" + (" CRITICALLY" if is_crit else ""), dmg, style
                )
                if not target.is_alive:
                    renderer.print_combat_action(target.name, "is defeated!", style="success")
            else:
                # Skill attack
                skill_id = next(
                    (sid for sid in skills_available if self.skill_registry.get(sid).name == action), None
                )
                if skill_id:
                    val, success = combat_system.player_skill_attack(player, skill_id, target, self.skill_registry)
                    if success:
                        skill = self.skill_registry.get(skill_id)
                        renderer.print_combat_action("You", f"use {skill.name}!", val, "rare")
                    else:
                        renderer.print_scene_text(["Not enough MP!"])
                    if not target.is_alive:
                        renderer.print_combat_action(target.name, "is defeated!", style="success")

            # Enemy turns
            for enemy in alive_enemies:
                if enemy.is_alive and player.current_hp > 0:
                    dmg = combat_system.enemy_attack(enemy, player)
                    if dmg == 0:
                        renderer.print_combat_action(enemy.name, "attacks — you dodge!", style="miss")
                    else:
                        renderer.print_combat_action(enemy.name, "attacks you for", dmg, "damage")

            alive_enemies = [e for e in enemies if e.is_alive]
            time.sleep(0.3)

        if player.current_hp <= 0:
            renderer.print_system_message("YOU HAVE FALLEN.", style="system_warning")
            if feature("lives_system"):
                from systems import lives_system
                zone_id = self.state.current_scene_id
                survived = lives_system.handle_death(self.state, cause=f"combat:{encounter_id}", zone_id=zone_id)
                bus.flush()
                if not survived:
                    renderer.print_system_message("ALL LIVES SPENT. GAME OVER.", style="system_warning")
                    renderer.print_scene_text(["The System erases your record. You are gone."])
                    renderer.prompt_any_key()
                    self._running = False
                else:
                    renderer.print_scene_text([
                        f"A life burns away. {player.lives_remaining} life{'s' if player.lives_remaining != 1 else ''} remaining.",
                        "You wake at your last safe point, battered but breathing.",
                    ])
                    renderer.prompt_any_key()
            else:
                renderer.print_scene_text(["The System records your defeat."])
                renderer.prompt_any_key()
                self._running = False
            return

        # Victory
        total_xp = sum(e.xp_reward for e in enemies)
        total_gold = sum(e.gold_reward for e in enemies)
        leveled_up = level_system.add_experience(player, total_xp)
        player.gold += total_gold

        renderer.print_divider()
        renderer.print_system_message("VICTORY", style="system_msg")
        from config import format_currency
        renderer.print_scene_text([f"XP gained: {total_xp}", f"Gold gained: {format_currency(total_gold)}"])
        if leveled_up:
            for lvl in leveled_up:
                renderer.print_system_message(f"LEVEL UP → {lvl}", style="system_msg")
            self._stat_allocation_prompt()

        bus.flush()
        renderer.prompt_any_key()

    def _run_dialogue(self, template_id: str) -> None:
        """Run a full NPC dialogue loop until __exit__ or no options remain."""
        if not feature("npc_system") or self.npc_registry is None:
            return

        from systems import npc_system
        from systems import quest_system as qs

        npc = self.npc_registry.get(template_id)
        if not npc:
            renderer.print_error(f"NPC not found: {template_id}")
            return

        # Visibility check (appears_requires)
        if not npc_system.check_npc_visible(npc, self.state):
            renderer.print_scene_text(["There is no one there that you can make out."])
            return

        # Register in world_db if first encounter
        if feature("world_db"):
            npc_system.ensure_npc_instance(npc, self.state)

        disposition = npc_system.get_npc_disposition(npc, self.state)
        current_node = npc_system.get_disposition_hook(npc, disposition)

        while True:
            bus.flush()
            resolved = npc_system.resolve_node(npc, current_node, self.state, disposition)

            renderer.clear()
            renderer.print_title()
            renderer.print_npc_dialogue(npc.name, npc.description, resolved.display_text)

            options = resolved.options
            if not options:
                break

            renderer.print_options(options)
            available = [(i + 1, opt) for i, opt in enumerate(options) if not opt.locked]
            if not available:
                renderer.print_scene_text(["You have nothing to say here."])
                renderer.prompt_any_key()
                break

            choice_labels = [f"{i}. {opt.label}" for i, opt in available]
            answer = questionary.select("Say:", choices=choice_labels).ask()
            if answer is None:
                break

            try:
                num = int(answer.split(".")[0])
                chosen_opt_scene = next((opt for i, opt in available if i == num), None)
            except (ValueError, IndexError):
                chosen_opt_scene = None

            if not chosen_opt_scene:
                break

            # Find the raw NPCDialogueOption matching the chosen SceneOption
            node_data = npc.dialogue_nodes.get(current_node)
            raw_opt = next(
                (o for o in (node_data.options if node_data else [])
                 if o.option_id == chosen_opt_scene.option_id),
                None,
            )
            if raw_opt is None:
                break

            # Show NPC response
            if raw_opt.npc_response:
                renderer.print_npc_response(npc.name, raw_opt.npc_response)

            # Apply effects and get engine-delegated triggers
            apply_result = npc_system.apply_option_effects(raw_opt, npc, self.state)

            # Handle engine-delegated triggers
            for eng_trigger in apply_result.engine_triggers:
                if eng_trigger.startswith("start_quest:") and feature("quest_system") and self.quest_registry:
                    quest_id = eng_trigger[12:]
                    qs.start_quest(quest_id, npc.npc_id, self.state, self.quest_registry)
                    bus.flush()

            # Refresh disposition after changes
            disposition = npc_system.get_npc_disposition(npc, self.state)

            next_node = raw_opt.leads_to_node
            if next_node == "__exit__":
                break
            current_node = next_node

        self.state.mark_dirty()
        renderer.prompt_any_key()

    def _stat_allocation_prompt(self) -> None:
        player = self.state.player
        while player.stat_points > 0:
            renderer.print_status_bar(player, self.item_registry)
            renderer.console.print(f"\n  [system_msg]You have {player.stat_points} stat point(s) to spend.[/system_msg]")
            stats = ["STR", "INT", "AGI", "LCK", "VIT", "WIS", "END", "Skip"]
            choice = questionary.select("Allocate stat point:", choices=stats).ask()
            if choice == "Skip" or choice is None:
                break
            level_system.spend_stat_point(player, choice)

        # Check for species evolution after stats are allocated
        if feature("species_system") and self.species_registry and player.species_id:
            from systems.species_system import check_evolution, trigger_evolution
            species = self.species_registry.get(player.species_id)
            if species:
                stage = check_evolution(player, species, self.skill_registry)
                if stage:
                    trigger_evolution(player, stage, species, self.skill_registry)
                    bus.flush()

    def _join_guild(self, guild_id: str) -> None:
        """Handle join_guild:guild_id trigger."""
        from systems import guild_system
        guild = self.guild_registry.get(guild_id)
        if not guild:
            renderer.print_error(f"Guild not found: {guild_id}")
            return
        ok, msg = guild_system.join_guild(self.state.player, guild, self.state)
        style = "success" if ok else "system_warning"
        renderer.print_system_message(msg, style=style)
        bus.flush()
        renderer.prompt_any_key()

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
        with renderer.show_ai_thinking_spinner(f"ANALYZING: {question[:40]}..."):
            result = self.ai_generator.generate_dynamic_options(
                question=question,
                scene_title=scene_title,
                scene_text=scene_text,
                current_options=option_labels,
                player=self.state.player,
            )

        if not result:
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
            )
            new_scene_options.append(scene_opt)

        # Store in state so they appear in the next render
        state_key = f"{self.state.current_scene_id}:{self.state.current_node_id}"
        self.state._dynamic_options[state_key] = new_scene_options

        renderer.console.print("  [dim_text]New options unlocked. Choose below.[/dim_text]")
        renderer.prompt_any_key()

    def _admin_panel(self) -> None:
        """Admin panel — feature flags, AI stats, debug tools."""
        while True:
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ ADMIN PANEL ][/system_msg]\n")

            bg_status = "ON" if self._bg_generator and self._bg_generator._running else "OFF"
            ai_status = "online" if self.ai_generator else "offline"

            choices = [
                f"AI Token Usage  (AI: {ai_status})",
                f"Toggle Feature Flags",
                f"Background Generator: {bg_status}",
                "God Mode (restore HP + MP)",
                "Grant Test Item",
                "Player Stats Dump",
                "← Back",
            ]
            action = questionary.select("Admin:", choices=choices).ask()
            if action is None or action == "← Back":
                break

            elif action.startswith("AI Token Usage"):
                if self.ai_generator is None:
                    renderer.print_system_message("AI system is offline.", style="dim_text")
                else:
                    stats = self.ai_generator.client.token_summary()
                    renderer.console.print()
                    renderer.console.print("  [system_msg][ OLLAMA TOKEN USAGE — THIS SESSION ][/system_msg]")
                    renderer.console.print(f"  Calls made       : [gold]{stats['calls']}[/gold]")
                    renderer.console.print(f"  Prompt tokens    : [gold]{stats['prompt_tokens']:,}[/gold]")
                    renderer.console.print(f"  Generated tokens : [gold]{stats['generated_tokens']:,}[/gold]")
                    renderer.console.print(f"  Total tokens     : [gold]{stats['total_tokens']:,}[/gold]")
                    renderer.console.print(f"  Model            : [dim_text]{self.ai_generator.model if self.ai_generator else 'N/A'}[/dim_text]")
                    bg_stats = f"  BG tasks queued  : [dim_text]{self._bg_generator._task_queue.qsize() if self._bg_generator else 0}[/dim_text]"
                    renderer.console.print(bg_stats)
                    renderer.console.print()
                renderer.prompt_any_key()

            elif action == "Toggle Feature Flags":
                self._admin_toggle_features()

            elif action.startswith("Background Generator"):
                if self._bg_generator:
                    if self._bg_generator._running:
                        self._bg_generator.stop()
                        renderer.print_system_message("Background generator stopped.", style="dim_text")
                    else:
                        self._bg_generator.start()
                        renderer.print_system_message("Background generator started.", style="success")
                else:
                    renderer.print_system_message("Background generator not initialized (AI offline).", style="dim_text")
                renderer.prompt_any_key()

            elif action == "God Mode (restore HP + MP)":
                self.state.player.current_hp = self.state.player.max_hp
                self.state.player.current_mp = self.state.player.max_mp
                renderer.print_success(f"HP restored to {self.state.player.max_hp}. MP restored to {self.state.player.max_mp}.")
                renderer.prompt_any_key()

            elif action == "Grant Test Item":
                all_items = [item.item_id for item in self.item_registry.all()]
                all_items.sort()
                all_items.append("← Cancel")
                selected = questionary.select("Select item to grant:", choices=all_items).ask()
                if selected and selected != "← Cancel":
                    self.state.player.add_item(selected, 1)
                    renderer.print_success(f"Granted: {selected}")
                    renderer.prompt_any_key()

            elif action == "Player Stats Dump":
                p = self.state.player
                from config import format_currency
                renderer.console.print()
                renderer.console.print(f"  Name      : {p.name}")
                renderer.console.print(f"  Level     : {p.level}  XP: {p.experience}/{p.experience_to_next}")
                renderer.console.print(f"  Class     : {p.active_class or p.base_class or 'Unclassified'}")
                renderer.console.print(f"  Gold      : {format_currency(p.gold)}")
                renderer.console.print(f"  HP/MP     : {p.current_hp}/{p.max_hp}  {p.current_mp}/{p.max_mp}")
                renderer.console.print(f"  Alignment : {p.alignment:+.1f} ({p.alignment_label})")
                renderer.console.print(f"  Turn      : {p.turn_count}")
                renderer.console.print(f"  Flags     : {list(p.flags.keys())}")
                renderer.console.print(f"  Skills    : {p.skills}")
                renderer.console.print(f"  Buffs     : {p.active_buffs}")
                renderer.console.print()
                renderer.prompt_any_key()

    def _admin_toggle_features(self) -> None:
        """Interactive feature flag toggle menu."""
        from config import FEATURES
        while True:
            renderer.clear()
            renderer.console.print("\n  [system_msg][ FEATURE FLAGS ][/system_msg]\n")
            flag_choices = []
            for flag, enabled in FEATURES.items():
                status = "[green]ON [/green]" if enabled else "[red]OFF[/red]"
                flag_choices.append(f"  {status}  {flag}")
            flag_choices.append("← Back")

            selected = questionary.select("Toggle flag:", choices=flag_choices).ask()
            if selected is None or selected == "← Back":
                break

            # Extract flag name from the display string
            for flag in FEATURES.keys():
                if flag in selected:
                    FEATURES[flag] = not FEATURES[flag]
                    new_status = "enabled" if FEATURES[flag] else "disabled"
                    renderer.print_system_message(f"{flag}: {new_status}", style="success")
                    renderer.console.print("  [dim_text](Note: some flags require restart to take full effect)[/dim_text]")
                    renderer.prompt_any_key()
                    break

    def _inventory_menu(self) -> None:
        """Interactive inventory and equipment management menu."""
        from systems.inventory_system import (
            equip_item, unequip_item, use_item,
            get_item_display_name, get_item_identify_hint,
        )
        from entities.enums import ItemType

        player = self.state.player

        while True:
            renderer.clear()
            renderer.print_title()
            renderer.print_status_bar(player, self.item_registry)
            renderer.print_divider()

            # Build item list with display names
            item_choices = []
            item_map = {}  # label → item_id
            for slot in player.inventory:
                item = self.item_registry.get(slot.item_id)
                if not item:
                    continue
                display = get_item_display_name(player, item, self.item_registry)
                qty_str = f" x{slot.quantity}" if slot.quantity > 1 else ""
                label = f"{display}{qty_str}"
                item_choices.append(label)
                item_map[label] = slot.item_id

            item_choices.append("← Close")

            answer = questionary.select("Select item:", choices=item_choices).ask()
            if answer is None or answer == "← Close":
                break

            item_id = item_map.get(answer)
            if not item_id:
                break

            item = self.item_registry.get(item_id)
            if not item:
                continue

            # Build sub-menu based on item type
            sub_choices = []
            if item.item_type in (ItemType.WEAPON, ItemType.ARMOR, ItemType.ACCESSORY):
                slot_name = item.item_type.value.lower()
                equipped_id = getattr(player.equipped, slot_name, None)
                if equipped_id == item_id:
                    sub_choices.append("Unequip")
                else:
                    sub_choices.append("Equip")
            elif item.item_type == ItemType.CONSUMABLE:
                sub_choices.append("Use")
                if "inspect" not in player.skills and item_id not in player.identified_items:
                    sub_choices.append("Inspect (guess)")
            elif item.item_type == ItemType.KEY_ITEM:
                sub_choices.append("Inspect")
            sub_choices.extend(["Drop", "← Back"])

            sub_answer = questionary.select(f"{item.name}:", choices=sub_choices).ask()
            if sub_answer is None or sub_answer == "← Back":
                continue

            if sub_answer == "Equip":
                ok, msg = equip_item(player, item_id, self.item_registry)
                renderer.print_system_message(msg, style="success" if ok else "system_warning")
                from systems.level_system import recalculate_derived_stats
                recalculate_derived_stats(player)
                renderer.prompt_any_key()

            elif sub_answer == "Unequip":
                slot_name = item.item_type.value.lower()
                ok, msg = unequip_item(player, slot_name, self.item_registry)
                renderer.print_system_message(msg, style="success" if ok else "system_warning")
                from systems.level_system import recalculate_derived_stats
                recalculate_derived_stats(player)
                renderer.prompt_any_key()

            elif sub_answer == "Use":
                ok, msg = use_item(player, item_id, self.item_registry)
                renderer.print_system_message(msg, style="success" if ok else "system_warning")
                bus.flush()
                renderer.prompt_any_key()

            elif sub_answer == "Inspect (guess)":
                hint = get_item_identify_hint(item)
                renderer.print_scene_text([hint])
                renderer.prompt_any_key()

            elif sub_answer == "Inspect":
                renderer.print_scene_text([item.description])
                if item.flavor_text:
                    renderer.console.print(f'  [italic dim_text]"{item.flavor_text}"[/italic dim_text]')
                renderer.prompt_any_key()

            elif sub_answer == "Drop":
                confirm = questionary.confirm(f"Drop {item.name}?", default=False).ask()
                if confirm:
                    player.remove_item(item_id, 1)
                    renderer.print_system_message(f"Dropped {item.name}.", style="dim_text")
                    renderer.prompt_any_key()

        self.state.mark_dirty()

    def _integrate_background_content(self) -> None:
        """Poll background generator results and integrate into live game state."""
        if not self._bg_generator:
            return
        results = self._bg_generator.poll_results()
        if not results:
            return

        integrated_any = False
        for result in results:
            try:
                rtype = result.get("type")
                if rtype == "quest" and feature("quest_system") and self.quest_registry:
                    template = result.get("template")
                    if template:
                        self.quest_registry.register(template)
                        if feature("world_db") and self.state.world_db:
                            self.state.world_db.store_bg_content(
                                "quest", template.template_id,
                                template.model_dump(),
                                zone_id=self.state.current_scene_id,
                                generated_turn=self.state.player.turn_count,
                            )
                        integrated_any = True

                elif rtype == "narrative":
                    zone_id = result.get("zone_id", "")
                    text = result.get("text", "")
                    if zone_id and text:
                        scene = self.scene_registry.get(zone_id)
                        if scene:
                            scene.entrance_text = [text]
                        if feature("world_db") and self.state.world_db:
                            self.state.world_db.store_bg_content(
                                "narrative", zone_id,
                                {"zone_id": zone_id, "text": text},
                                zone_id=zone_id,
                                generated_turn=self.state.player.turn_count,
                            )
                        integrated_any = True

                elif rtype == "npc_branch" and feature("npc_system") and self.npc_registry:
                    npc_id = result.get("npc_id", "")
                    node = result.get("node", {})
                    if npc_id and node:
                        npc = self.npc_registry.get(npc_id)
                        if npc and hasattr(npc, "dialogue_nodes") and node.get("node_id"):
                            from entities.npc import NPCDialogueNode
                            try:
                                npc.dialogue_nodes[node["node_id"]] = NPCDialogueNode.model_validate(node)
                            except Exception:
                                pass  # Malformed node — skip silently
                        if feature("world_db") and self.state.world_db:
                            self.state.world_db.store_bg_content(
                                "npc_branch", npc_id, node,
                                generated_turn=self.state.player.turn_count,
                            )
                        integrated_any = True

            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(f"Content integration error: {exc}")

        if integrated_any:
            renderer.console.print("\n  [dim_text][ New content discovered nearby ][/dim_text]")

    def _maybe_submit_background_task(self) -> None:
        """Submit background generation tasks at configured intervals."""
        if not self._bg_generator or not self.state:
            return
        from config import BG_GEN_INTERVAL

        player = self.state.player
        turn = player.turn_count

        # Skip during turn 0 and non-interval turns
        if turn == 0 or turn % BG_GEN_INTERVAL != 0:
            return

        zone_id = self.state.current_scene_id
        player_profile = {
            "level": player.level,
            "alignment": player.alignment,
            "active_class": player.active_class or player.base_class or "Unclassified",
            "flags": list(player.flags.keys())[:10],  # cap for prompt size
        }

        # Submit quest generation every interval
        self._bg_generator.submit_quest(
            zone_id=zone_id,
            npc_hint="a stranger in the area",
            player_profile=player_profile,
        )

        # Submit zone narrative the first time a zone is visited
        bg_narrative_flag = f"_bg_narrative_submitted:{zone_id}"
        if not player.has_flag(bg_narrative_flag):
            player.set_flag(bg_narrative_flag)
            scene = self.scene_registry.get(zone_id)
            zone_name = scene.title if scene else zone_id
            context_flags = [k for k in player.flags if not k.startswith("_")][:8]
            self._bg_generator.submit_zone_narrative(zone_id, zone_name, context_flags)

    def _save_prompt(self) -> None:
        slot = questionary.text("Save slot name:", default=self.state.player.name.lower().replace(" ", "_")).ask()
        if slot:
            path = save_game(self.state, slot, SAVES_DIR)
            renderer.print_success(f"Game saved to {path.name}")
            time.sleep(0.5)
