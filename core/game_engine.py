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
from config import SAVES_DIR, DATA_DIR, AI_ENABLED, OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_MAX_RETRIES

if TYPE_CHECKING:
    pass


class GameEngine:
    def __init__(self) -> None:
        self.class_registry = ClassRegistry()
        self.skill_registry = SkillRegistry()
        self.item_registry = ItemRegistry()
        self.scene_registry = SceneRegistry()
        self.system_messages: dict = {}
        self.lore_data: dict = {}
        self.state: GameState | None = None
        self.ai_generator = None
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

    def _setup_ai(self) -> None:
        if not AI_ENABLED:
            return
        try:
            from ai.ollama_client import OllamaClient
            from ai.content_generator import ContentGenerator
            client = OllamaClient(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
            if client.is_available():
                self.ai_generator = ContentGenerator(client, self.lore_data, OLLAMA_MODEL)
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
                self.state.advance_turn()
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

    def _render_scene(self) -> None:
        renderer.clear()
        renderer.print_title()

        scene = self.scene_registry.get(self.state.current_scene_id)
        if not scene:
            renderer.print_error(f"Scene not found: {self.state.current_scene_id}")
            return

        # Show status bar at top for non-prologue scenes
        if self.state.current_scene_id not in ("prologue", "character_creation"):
            renderer.print_status_bar(self.state.player)
            renderer.print_divider()

        renderer.print_scene_header(scene.title)

        # Show entrance text only on first visit to root node
        node_id = self.state.current_node_id
        if node_id == "root" and scene.entrance_text:
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
        return scene.get_options(self.state, self.state.current_node_id)

    def _prompt_choice(self, options: list[SceneOption]) -> SceneOption | None:
        renderer.print_options(options)

        # Build questionary choices (skip locked ones)
        available = [(i + 1, opt) for i, opt in enumerate(options) if not opt.locked]
        if not available:
            renderer.print_error("All options are locked.")
            return None

        extra = ["[S] Save game", "[Q] Quit to menu"]
        choice_labels = [f"{i}. {opt.label}" for i, opt in available] + extra
        answer = questionary.select("Choose:", choices=choice_labels).ask()

        if answer is None or answer.startswith("[Q]"):
            self._running = False
            return None
        if answer.startswith("[S]"):
            self._save_prompt()
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

        # Refresh item names in notifications now that we may have them
        bus.flush()

        # Transition
        if option.leads_to and option.leads_to != "__stay__":
            self.state.current_scene_id = option.leads_to
            self.state.current_node_id = option.leads_to_node or "root"
        else:
            self.state.current_node_id = option.leads_to_node or "root"

        self.state.mark_dirty()

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
            renderer.print_status_bar(player)
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
        renderer.print_scene_text([f"XP gained: {total_xp}", f"Gold gained: {total_gold}"])
        if leveled_up:
            for lvl in leveled_up:
                renderer.print_system_message(f"LEVEL UP → {lvl}", style="system_msg")
            self._stat_allocation_prompt()

        bus.flush()
        renderer.prompt_any_key()

    def _stat_allocation_prompt(self) -> None:
        player = self.state.player
        while player.stat_points > 0:
            renderer.print_status_bar(player)
            renderer.console.print(f"\n  [system_msg]You have {player.stat_points} stat point(s) to spend.[/system_msg]")
            stats = ["STR", "INT", "AGI", "LCK", "VIT", "WIS", "END", "Skip"]
            choice = questionary.select("Allocate stat point:", choices=stats).ask()
            if choice == "Skip" or choice is None:
                break
            level_system.spend_stat_point(player, choice)

    def _save_prompt(self) -> None:
        slot = questionary.text("Save slot name:", default=self.state.player.name.lower().replace(" ", "_")).ask()
        if slot:
            path = save_game(self.state, slot, SAVES_DIR)
            renderer.print_success(f"Game saved to {path.name}")
            time.sleep(0.5)
