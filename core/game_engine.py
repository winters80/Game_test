from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

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
from ui.notifications import setup_notification_listeners
from config import SAVES_DIR, DATA_DIR, AI_ENABLED, OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_MAX_RETRIES, feature, GUILD_SIM_INTERVAL
from entities.enums import SkillType

if TYPE_CHECKING:
    pass


# ── Bot goal cycling ──────────────────────────────────────────────────────────

_BOT_GOAL_CYCLE: dict[str, str] = {
    "explore": "trade",
    "trade":   "rest",
    "rest":    "explore",
    "combat":  "rest",
    "idle":    "explore",
}


def _advance_bot_goal(bot: "Any") -> None:
    """Cycle the bot's current goal and trim memory to last 10 entries."""
    from typing import Any as _Any  # noqa: F401 (type hint only)
    bot.current_goal = _BOT_GOAL_CYCLE.get(bot.current_goal, "explore")
    if len(bot.memory) > 20:
        bot.memory = bot.memory[-10:]


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
        self._world_director = None
        self._bot_manager = None
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

        from systems.world_zones import load_zones
        load_zones(DATA_DIR)

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
            from systems.guilds.guild_loader import load_guild_registry
            self.guild_registry = load_guild_registry(DATA_DIR)

        if feature("faction_system"):
            from entities.faction import FactionRegistry
            self.faction_registry = FactionRegistry()
            self.faction_registry.load_from_file(DATA_DIR / "factions" / "faction_definitions.json")

        if feature("bot_system"):
            from systems.bot_system import BotRegistry, BotManager
            bot_registry = BotRegistry()
            bot_path = DATA_DIR / "bots" / "bot_templates.json"
            if bot_path.exists():
                bot_registry.load_from_file(bot_path)
            self._bot_manager = BotManager()
            self._bot_manager.load_from_templates(bot_registry)

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
                if self._bg_generator and self.ai_generator:
                    from ai.world_director import WorldDirector
                    self._world_director = WorldDirector(
                        self._bg_generator,
                        self.ai_generator.client,
                        self.ai_generator.lore_data,
                    )
                    logger.info("WorldDirector initialized.")
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
        slot_name = player.name.lower().replace(" ", "_")
        self.state = new_game_state(player, SAVES_DIR, slot_name)
        self.state.skill_registry = self.skill_registry
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

        extra = ["[?] Ask about this situation", "[K] Skills", "[I] Items & Equipment", "[L] World Log", "[S] Save game", "[A] Admin Panel", "[Q] Quit to menu"]
        if feature("crafting_system"):
            player = self.state.player
            if player.has_flag("alchemist") or player.has_flag("crafter"):
                extra.insert(2, "[C] Craft")
        if feature("guild_system"):
            extra.insert(-1, "[G] Found a Guild")
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
                # Surprise round if the option was an AI ambush/sneak option
                _surprise = (
                    option.option_id.startswith("ai_") and
                    any(kw in option.label.lower() for kw in ("ambush", "sneak", "surprise", "ledge", "stealth"))
                )
                self._run_combat(encounter_id, surprise=_surprise)
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
            elif trigger.startswith("rest_camp:"):
                rest_type = trigger[10:]
                self._handle_rest(rest_type)
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

        # AI-generated options: show narrative feedback, then remove the used option
        if option.option_id.startswith("ai_"):
            state_key = f"{self.state.current_scene_id}:{self.state.current_node_id}"
            narrative_text = ""
            if option.narrative:
                renderer.print_divider()
                renderer.print_scene_text([f"» {option.narrative}"])
                renderer.print_divider()
                narrative_text = option.narrative
            else:
                renderer.print_system_message("Action taken.", style="dim_text")
            # Remove just this option so it can't be spammed; keep others
            dynamic = self.state._dynamic_options.get(state_key, [])
            self.state._dynamic_options[state_key] = [
                o for o in dynamic if o.option_id != option.option_id
            ]

            # Check if narrative implies combat
            combat_keywords = ("combat", "fight", "attack", "confrontation", "standoff", "strikes", "lunges", "draws weapon")
            if narrative_text and any(kw in narrative_text.lower() for kw in combat_keywords):
                renderer.prompt_any_key()
                self._run_combat("street_thugs")
            elif self.ai_generator and narrative_text:
                # Generate 2-3 follow-up options from Ollama
                followup_opts = self._generate_ai_followup(narrative_text)
                if followup_opts:
                    from scenes.scene_base import SceneOption
                    new_followups = []
                    for i, fo in enumerate(followup_opts[:3]):
                        new_followups.append(SceneOption(
                            option_id=f"ai_followup_{i}",
                            label=f"[AI] {fo}",
                            leads_to="__stay__",
                            leads_to_node=self.state.current_node_id,
                            expected=False,
                            triggers=[],
                            narrative="",
                        ))
                    # Prepend follow-ups so they appear at top of dynamic options
                    existing = self.state._dynamic_options.get(state_key, [])
                    self.state._dynamic_options[state_key] = new_followups + existing
                    renderer.console.print("  [dim_text]New options available.[/dim_text]")
                    renderer.prompt_any_key()
                else:
                    renderer.prompt_any_key()
            else:
                renderer.prompt_any_key()

        # Transition
        if option.leads_to and option.leads_to != "__stay__":
            logger.info(
                "Scene transition: %s → %s (node: %s) via option '%s'",
                self.state.current_scene_id, option.leads_to,
                option.leads_to_node or "root", option.option_id,
            )
            self.state.current_scene_id = option.leads_to
            self.state.current_node_id = option.leads_to_node or "root"
            # Clear all dynamic options for the new node on real navigation
            new_key = f"{self.state.current_scene_id}:{self.state.current_node_id}"
            self.state._dynamic_options.pop(new_key, None)
        else:
            self.state.current_node_id = option.leads_to_node or "root"

        self.state.mark_dirty()

    def _generate_ai_followup(self, narrative_text: str) -> list[str]:
        """
        Ask the AI for 2-3 immediate follow-up options given a narrative outcome.
        Returns a list of short option label strings, or empty list on failure.
        """
        if not self.ai_generator:
            return []
        try:
            scene = self.scene_registry.get(self.state.current_scene_id)
            scene_title = scene.title if scene else self.state.current_scene_id
            prompt = (
                f"Scene: {scene_title}\n"
                f"What just happened: {narrative_text}\n\n"
                "Given this outcome, list 2-3 immediate short options the player could choose next. "
                "Each option must be a single short sentence (under 12 words). "
                "Return ONLY a JSON array of strings, e.g.: "
                '[\"Press the advantage.\", \"Step back and assess.\", \"Call out to the others.\"]'
            )
            system_prompt = (
                "You write concise player action options for a fantasy LitRPG text game set in Aethoria. "
                "Return ONLY a valid JSON array of 2-3 short option strings. No explanation."
            )
            with renderer.show_ai_thinking_spinner("Generating follow-up options..."):
                result = self.ai_generator.client.generate_json(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0.75,
                    timeout=15,
                    max_retries=1,
                )
            if isinstance(result, list):
                return [str(r) for r in result if isinstance(r, str)]
            # Some models return {"options": [...]}
            if isinstance(result, dict):
                for key in ("options", "choices", "actions"):
                    if key in result and isinstance(result[key], list):
                        return [str(r) for r in result[key] if isinstance(r, str)]
        except Exception as e:
            logger.warning("AI follow-up generation failed: %s", e)
        return []

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

    def _run_combat(self, encounter_id: str, surprise: bool = False) -> None:
        enemies = combat_system.spawn_encounter(encounter_id)
        if not enemies:
            return

        # Give numbered display names to duplicates — "Goblin Scout #1", "Goblin Scout #2"
        _name_counts: dict[str, int] = {}
        for e in enemies:
            _name_counts[e.name] = _name_counts.get(e.name, 0) + 1
        _name_idx: dict[str, int] = {}
        for e in enemies:
            if _name_counts[e.name] > 1:
                _name_idx[e.name] = _name_idx.get(e.name, 0) + 1
                e.name = f"{e.name} #{_name_idx[e.name]}"

        renderer.print_divider()
        count = len(enemies)
        names_str = ", ".join(e.name for e in enemies)
        renderer.print_system_message(
            f"COMBAT INITIATED — {count} enem{'y' if count == 1 else 'ies'}: {names_str}",
            style="system_warning",
        )
        time.sleep(0.5)

        player = self.state.player
        turn = 0
        alive_enemies = [e for e in enemies if e.is_alive]

        # Surprise round: player gets one free attack before enemies can respond
        if surprise and alive_enemies:
            target = alive_enemies[0]
            dmg, is_crit = combat_system.player_attack(player, target)
            crit_str = " CRITICALLY" if is_crit else ""
            renderer.print_combat_action(
                "SURPRISE!", f"You strike {target.name}{crit_str} before they react!", dmg, "system_warning"
            )
            if not target.is_alive:
                renderer.print_combat_action(target.name, "goes down before the fight begins!", style="success")
            alive_enemies = [e for e in enemies if e.is_alive]
            time.sleep(0.4)

        def _enemy_bar(e) -> str:
            pct = e.current_hp / e.max_hp if e.max_hp > 0 else 0
            filled = int(pct * 10)
            bar = "█" * filled + "░" * (10 - filled)
            color = "hp_high" if pct > 0.6 else ("hp_mid" if pct > 0.3 else "hp_low")
            return f"[{color}]{bar}[/{color}] {e.current_hp}/{e.max_hp}"

        def _skill_label(sid: str) -> str:
            sk = self.skill_registry.get(sid)
            if sk is None:
                return sid
            label = sk.name
            cd = player.skill_cooldowns.get(sid, 0)
            if cd > 0:
                label += f" [CD:{cd}]"
            elif sk.mp_cost > 0:
                label += f" [{sk.mp_cost} MP]"
            return label

        while alive_enemies and player.current_hp > 0:
            turn += 1
            renderer.clear()
            renderer.print_title()
            renderer.print_status_bar(player, self.item_registry)
            renderer.print_divider()

            # Show all enemies with HP bars
            renderer.console.print(f"  [system_warning]⚔  TURN {turn}[/system_warning]")
            for i, enemy in enumerate(alive_enemies, 1):
                renderer.console.print(
                    f"  [{i}] [damage]{enemy.name}[/damage]  {_enemy_bar(enemy)}  "
                    f"[dim_text]ATK {enemy.attack}  DEF {enemy.defense}[/dim_text]"
                )
            renderer.console.print()

            # ── Choose action ─────────────────────────────────────────────────
            # Only include ACTIVE and TRIGGERED skills — skip PASSIVE skills
            skills_available = [
                sid for sid in player.skills
                if (sk := self.skill_registry.get(sid)) and sk.skill_type != SkillType.PASSIVE
            ]
            action_choices = ["⚔ Basic Attack"] + [_skill_label(sid) for sid in skills_available] + ["🏃 Flee"]
            action = questionary.select("Your action:", choices=action_choices).ask()

            if action is None:
                # Treat cancellation as re-prompt (loop continues naturally)
                continue

            if action == "🏃 Flee":
                if combat_system.try_flee(player, alive_enemies[0]):
                    renderer.print_scene_text(["You flee from combat!"])
                    renderer.prompt_any_key()
                    return
                else:
                    renderer.print_scene_text(["You failed to flee!"])

            else:
                # ── Choose target (if more than one enemy alive) ──────────────
                if len(alive_enemies) > 1 and action != "🏃 Flee":
                    target_choices = [
                        f"[{i}] {e.name}  ({e.current_hp}/{e.max_hp} HP)"
                        for i, e in enumerate(alive_enemies, 1)
                    ]
                    t_answer = questionary.select("Target:", choices=target_choices).ask()
                    try:
                        t_idx = int(t_answer.split("]")[0].lstrip("[")) - 1
                        target = alive_enemies[t_idx]
                    except Exception:
                        target = alive_enemies[0]
                else:
                    target = alive_enemies[0]

                # ── Execute action ────────────────────────────────────────────
                if action == "⚔ Basic Attack":
                    dmg, is_crit = combat_system.player_attack(player, target)
                    crit_str = " CRITICALLY" if is_crit else ""
                    renderer.print_combat_action(
                        "You", f"strike {target.name}{crit_str} for", dmg,
                        "critical" if is_crit else "damage",
                    )
                    renderer.console.print(
                        f"  [dim_text]→ {target.name}: {target.current_hp}/{target.max_hp} HP remaining[/dim_text]"
                    )
                    if not target.is_alive:
                        renderer.print_combat_action(target.name, "is defeated!", style="success")

                else:
                    # Skill / spell
                    skill_id = next(
                        (sid for sid in skills_available if _skill_label(sid) == action), None
                    )
                    if skill_id:
                        skill = self.skill_registry.get(skill_id)
                        cd = player.skill_cooldowns.get(skill_id, 0)
                        if cd > 0:
                            renderer.print_scene_text([f"{skill.name} is on cooldown ({cd} turns remaining)."])
                        elif skill.mp_cost > 0 and player.current_mp < skill.mp_cost:
                            renderer.print_scene_text([f"Not enough MP! Need {skill.mp_cost}, have {player.current_mp}."])
                        else:
                            from entities.enums import EffectType
                            is_heal = skill.effects and any(
                                e.effect_type == EffectType.HEAL for e in skill.effects
                            )
                            if is_heal:
                                from systems.skill_system import calculate_skill_damage
                                heal_val = calculate_skill_damage(skill, player)
                                player.current_mp -= skill.mp_cost
                                player.current_hp = min(player.max_hp, player.current_hp + heal_val)
                                if skill.cooldown_turns > 0:
                                    player.skill_cooldowns[skill_id] = skill.cooldown_turns
                                renderer.print_scene_text([
                                    f"You cast {skill.name}. Healed {heal_val} HP. "
                                    f"({player.current_hp}/{player.max_hp})"
                                ])
                            else:
                                val, success = combat_system.player_skill_attack(
                                    player, skill_id, target, self.skill_registry
                                )
                                if success:
                                    if skill.cooldown_turns > 0:
                                        player.skill_cooldowns[skill_id] = skill.cooldown_turns
                                    renderer.print_combat_action(
                                        "You", f"cast {skill.name} on {target.name} for", val, "rare"
                                    )
                                    renderer.console.print(
                                        f"  [dim_text]→ {target.name}: {target.current_hp}/{target.max_hp} HP remaining[/dim_text]"
                                    )
                                else:
                                    renderer.print_scene_text(["Not enough MP!"])
                                if not target.is_alive:
                                    renderer.print_combat_action(target.name, "is defeated!", style="success")

            # ── Enemy turns ───────────────────────────────────────────────────
            for enemy in alive_enemies:
                if enemy.is_alive and player.current_hp > 0:
                    dmg = combat_system.enemy_attack(enemy, player)
                    if dmg == 0:
                        renderer.print_combat_action(enemy.name, "attacks — you dodge!", style="miss")
                    else:
                        renderer.print_combat_action(
                            enemy.name, f"strikes you for", dmg, "damage"
                        )
                        renderer.console.print(
                            f"  [dim_text]→ Your HP: {player.current_hp}/{player.max_hp}[/dim_text]"
                        )

            alive_enemies = [e for e in enemies if e.is_alive]
            time.sleep(0.3)

        if player.current_hp <= 0:
            renderer.print_system_message("YOU HAVE FALLEN.", style="system_warning")
            logger.warning(
                "Player died in combat: encounter=%s zone=%s level=%d hp=%d/%d lives=%d",
                encounter_id, self.state.current_scene_id,
                player.level, player.current_hp, player.max_hp, player.lives_remaining,
            )
            if feature("lives_system"):
                from systems import lives_system
                zone_id = self.state.current_scene_id
                survived = lives_system.handle_death(self.state, cause=f"combat:{encounter_id}", zone_id=zone_id)
                bus.flush()
                if not survived:
                    logger.warning("Player game-over: all lives spent. encounter=%s", encounter_id)
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

        # Schedule / location check — NPC may have moved to another zone
        if npc.schedule:
            from systems.npc_system import get_npc_zone
            npc_zone = get_npc_zone(npc, self.state.player.turn_count)
            if npc_zone != self.state.current_scene_id:
                phase = "day" if (self.state.player.turn_count % 20) < 10 else "night"
                renderer.print_system_message(
                    f"{npc.name} isn't here right now. "
                    f"They're usually in {npc_zone.replace('_', ' ').title()} during the {phase}.",
                    style="dim",
                )
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

            # If any option is a shop/purchase option, show player's current gold
            if any(
                any(t.startswith("buy_item:") for t in (opt.triggers or []))
                for opt in options
            ):
                from config import format_currency as _fc
                renderer.console.print(
                    f"  [gold]Your gold: {_fc(self.state.player.gold)}[/gold]\n"
                )

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

            # ── Pre-check: buy_item gold gate (must happen BEFORE npc_response) ──
            buy_trigger = next(
                (t for t in raw_opt.triggers if t.startswith("buy_item:")), None
            )
            if buy_trigger:
                parts = buy_trigger[9:].split(":")
                if len(parts) == 2:
                    try:
                        buy_price = int(parts[1])
                    except ValueError:
                        buy_price = 0
                    from config import format_currency as _fc
                    if self.state.player.gold < buy_price:
                        # Can't afford — AI shopkeeper broke comment
                        renderer.print_error(
                            f"Not enough gold. Need {_fc(buy_price)}, you have {_fc(self.state.player.gold)}."
                        )
                        broke_comment = "Come back when your coin purse is heavier."
                        if self.ai_generator:
                            try:
                                with renderer.show_ai_thinking_spinner(f"{npc.name} considers..."):
                                    raw_comment = self.ai_generator.client.generate_text(
                                        prompt=(
                                            f"You are {npc.name}, a {npc.role} in a fantasy city. "
                                            f"A customer wants to buy something costing {_fc(buy_price)} "
                                            f"but only has {_fc(self.state.player.gold)}. "
                                            f"Reply in character, 1-2 short sentences. "
                                            f"You may offer them a small errand or job to earn coin, "
                                            f"or make a dry but not cruel remark."
                                        ),
                                        system_prompt=(
                                            "You write brief, flavourful NPC dialogue for a fantasy RPG. "
                                            "Stay in character. No quotation marks around the response."
                                        ),
                                        temperature=0.85,
                                        max_tokens=80,
                                    )
                                if raw_comment:
                                    broke_comment = raw_comment.strip().strip('"')
                            except Exception:
                                pass
                        renderer.print_npc_response(npc.name, broke_comment)
                        renderer.prompt_any_key()
                        continue  # stay on current_node — don't advance

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

                elif eng_trigger.startswith("buy_item:"):
                    # Gold was already validated above; execute purchase
                    parts = eng_trigger[9:].split(":")
                    if len(parts) == 2:
                        buy_item_id = parts[0]
                        try:
                            buy_price = int(parts[1])
                        except ValueError:
                            buy_price = 0
                        from config import format_currency as _fc
                        self.state.player.gold -= buy_price
                        self.state.player.add_item(buy_item_id)
                        from core.event_bus import Event
                        bus.publish(Event("ITEM_FOUND", {"item_id": buy_item_id}))
                        renderer.print_success(
                            f"Paid {_fc(buy_price)}.  Gold remaining: {_fc(self.state.player.gold)}"
                        )
                        bus.flush()

            # Tick quests immediately after dialogue effects (catches instant completions)
            if feature("quest_system") and self.quest_registry:
                qs.tick_quests(self.state, self.quest_registry)
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

    def _found_guild_menu(self) -> None:
        """Interactive flow for founding a new guild."""
        from systems.guilds.guild_models import FoundGuildIntent
        from systems.guilds import guild_repo
        from systems.guilds.guild_loader import save_generated_template, make_default_template
        import uuid as _uuid

        renderer.clear()
        renderer.print_system_message("FOUND A GUILD", style="system_msg")

        name = questionary.text("Guild name:").ask()
        if not name or not name.strip():
            return
        name = name.strip()

        archetype = questionary.select(
            "Guild archetype:",
            choices=["combat", "stealth", "arcane", "merchant"],
        ).ask()
        if not archetype:
            return

        reason = questionary.text("Founding reason (optional, press Enter to skip):").ask() or ""

        intent = FoundGuildIntent(
            name=name,
            archetype=archetype,
            zone_id=self.state.current_scene_id or "verath_market",
            founding_reason=reason,
            initial_members=[],
        )

        # AI generates template if enabled
        template = None
        if self.ai_generator:
            try:
                renderer.console.print("  [dim_text]Consulting the System...[/dim_text]")
                template = self.ai_generator.generate_guild_template(
                    name=name,
                    archetype=archetype,
                    zone_id=intent.zone_id,
                    founding_reason=reason,
                    seed_traits=[],
                )
            except Exception:
                pass

        if template is None:
            guild_id = "gen_" + _uuid.uuid4().hex[:8]
            template = make_default_template(
                guild_id=guild_id, name=name, archetype=archetype,
                zone_id=intent.zone_id, founding_reason=reason,
            )

        # Persist template JSON and register in memory
        if self.guild_registry:
            try:
                save_generated_template(template, DATA_DIR / "guilds" / "generated")
            except Exception:
                pass
            self.guild_registry.register(template)

        # Create runtime state if world_db available
        if self.state.world_db is not None:
            guild_state = guild_repo.found_guild(
                world_db=self.state.world_db,
                intent=intent,
                template_id=template.guild_id,
                turn=self.state.player.turn_count,
                player_id=self.state.player.player_id,
            )
            if guild_state:
                self.state.player.guild_memberships[guild_state.guild_id] = "leader"
                renderer.print_system_message(
                    f"Guild '{template.name}' founded! You are its first leader.",
                    style="success",
                )
            else:
                renderer.print_error("Failed to create guild state in database.")
        else:
            renderer.print_system_message(
                f"Guild '{template.name}' founded (no database — state not persisted).",
                style="system_warning",
            )

        bus.flush()

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
        with renderer.show_ai_thinking_spinner(f"ANALYZING: {question[:40]}..."):
            result = self.ai_generator.generate_dynamic_options(
                question=question,
                scene_title=scene_title,
                scene_text=scene_text,
                current_options=option_labels,
                player=self.state.player,
            )

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

    # ── Skills menu ───────────────────────────────────────────────────────────

    def _skills_menu(self) -> None:
        """Show all learned skills grouped by type with full detail view."""
        from ui.panels import RARITY_COLORS
        player = self.state.player

        if not player.skills:
            renderer.print_system_message("You have not learned any skills yet.", style="dim_text")
            renderer.prompt_any_key()
            return

        # Group skills by type
        groups: dict[str, list] = {"ACTIVE": [], "PASSIVE": [], "TRIGGERED": [], "OTHER": []}
        for skill_id in player.skills:
            skill = self.skill_registry.get(skill_id)
            if not skill:
                continue
            key = skill.skill_type.value if skill.skill_type.value in groups else "OTHER"
            groups[key].append(skill)

        # Build flat choice list with headers
        skill_choices: list[str] = []
        skill_map: dict[str, object] = {}
        for group_name in ("ACTIVE", "PASSIVE", "TRIGGERED", "OTHER"):
            skills = groups[group_name]
            if not skills:
                continue
            skill_choices.append(f"── {group_name} ──")
            for skill in skills:
                ai_tag = " ✦" if skill.is_ai_generated else ""
                label = f"  {skill.name}{ai_tag}  [{skill.rarity.value}]"
                skill_choices.append(label)
                skill_map[label] = skill
        skill_choices.append("← Back")

        while True:
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ SKILLS ][/system_msg]\n")
            chosen = questionary.select("Select skill to inspect:", choices=skill_choices).ask()
            if chosen is None or chosen == "← Back" or chosen.startswith("──"):
                break
            skill = skill_map.get(chosen)
            if not skill:
                continue

            # Detail view
            renderer.clear()
            renderer.print_title()
            color = RARITY_COLORS.get(skill.rarity.value, "white")
            renderer.console.print(f"\n  [{color}]{skill.name}[/{color}]  [{color}]{skill.rarity.value}[/{color}]")
            if skill.is_ai_generated:
                renderer.console.print("  [cyan]✦ System-Awakened[/cyan]")
            renderer.console.print(f"\n  [scene_text]{skill.description}[/scene_text]")
            if skill.flavor_text:
                renderer.console.print(f'  [italic dim_text]"{skill.flavor_text}"[/italic dim_text]')
            renderer.console.print()
            if skill.skill_type:
                renderer.console.print(f"  Type    : [dim_text]{skill.skill_type.value}[/dim_text]")
            if skill.mp_cost > 0:
                renderer.console.print(f"  MP Cost : [mp]{skill.mp_cost}[/mp]")
            if skill.spell_type:
                renderer.console.print(f"  Element : [cyan]{skill.spell_type.upper()}[/cyan]")
            if skill.cooldown_turns > 0:
                cd_remaining = player.skill_cooldowns.get(skill.skill_id, 0)
                cd_str = f"  [dim_text](on cooldown: {cd_remaining} turns)[/dim_text]" if cd_remaining > 0 else ""
                renderer.console.print(f"  Cooldown: {skill.cooldown_turns} turns{cd_str}")
            if skill.effects:
                renderer.console.print("  Effects :")
                for eff in skill.effects:
                    stat = eff.scaling_stat or "—"
                    renderer.console.print(f"    [dim_text]{eff.effect_type.value.upper()}  base={eff.base_value}  ×{eff.scaling_coefficient} {stat}[/dim_text]")
            renderer.console.print()
            renderer.prompt_any_key()

    # ── Crafting menu ─────────────────────────────────────────────────────────

    def _craft_menu(self) -> None:
        """Alchemy / crafting menu — combine ingredients into items."""
        from systems.alchemy_system import load_recipes, craft_item
        recipes = load_recipes(DATA_DIR)
        if not recipes:
            renderer.print_system_message("No recipes are known yet.", style="dim_text")
            renderer.prompt_any_key()
            return

        while True:
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ CRAFTING ][/system_msg]\n")
            choices = [r["name"] for r in recipes] + ["← Back"]
            selected = questionary.select("Select recipe:", choices=choices).ask()
            if selected is None or selected == "← Back":
                break
            recipe = next((r for r in recipes if r["name"] == selected), None)
            if not recipe:
                continue
            # Show ingredients required
            renderer.console.print()
            renderer.console.print(f"  [scene_title]{recipe['name']}[/scene_title]")
            for ing in recipe.get("ingredients", []):
                item = self.item_registry.get(ing["item_id"])
                name = item.name if item else ing["item_id"]
                has = "✓" if self.state.player.has_item(ing["item_id"]) else "✗"
                renderer.console.print(f"    [{has}] {name} x{ing['qty']}")
            renderer.console.print()
            confirm = questionary.select("Craft?", choices=["Yes — craft it", "← Back"]).ask()
            if confirm == "Yes — craft it":
                ok, msg = craft_item(self.state.player, recipe["recipe_id"], self.item_registry, recipes)
                renderer.print_system_message(msg, style="success" if ok else "system_warning")
                renderer.prompt_any_key()

    def _admin_bots_panel(self) -> None:
        """Full bot agent viewer — list all bots, drill into character sheet."""
        from config import format_currency
        from ui.panels import RARITY_COLORS

        while True:
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ ACTIVE AGENTS ][/system_msg]\n")
            renderer.console.print(f"  Players : [gold]1[/gold]  (local session)")

            if not self._bot_manager or self._bot_manager.active_count() == 0:
                renderer.console.print("  AI Bots : [dim_text]0  (bot_system disabled or no bots loaded)[/dim_text]")
                renderer.console.print()
                renderer.prompt_any_key()
                return

            bots = self._bot_manager.all()
            renderer.console.print(f"  AI Bots : [cyan]{len(bots)}[/cyan]\n")

            bot_choices = []
            for bot in bots:
                bot_choices.append(
                    f"  {bot.name}  [{bot.bot_id}]  zone: {bot.current_zone_id}  goal: {bot.current_goal}"
                )
            bot_choices.append("← Back")

            chosen = questionary.select("Select bot to inspect:", choices=bot_choices).ask()
            if chosen is None or chosen == "← Back":
                break

            # Find the selected bot
            bot_idx = bot_choices.index(chosen)
            bot = bots[bot_idx]

            # Full character sheet
            renderer.clear()
            renderer.print_title()
            renderer.console.print(f"\n  [cyan]═══  {bot.name}  ═══[/cyan]  [dim_text][{bot.bot_id}][/dim_text]\n")
            renderer.console.print(f"  Personality : [dim_text]{bot.personality_seed}[/dim_text]")
            renderer.console.print(f"  Current Goal: [system_msg]{bot.current_goal}[/system_msg]")
            renderer.console.print(f"  Zone        : {bot.current_zone_id}")
            renderer.console.print(f"  Last Active : turn {bot.turn_last_acted}")
            renderer.console.print(f"  Gold        : [gold]{format_currency(bot.gold)}[/gold]")

            # Alignment
            align = bot.disposition
            if align >= 50:   align_label = "Virtuous"
            elif align >= 20: align_label = "Good-Natured"
            elif align >= -20: align_label = "Neutral"
            elif align >= -50: align_label = "Morally Gray"
            else:              align_label = "Corrupt"
            align_sign = "+" if align >= 0 else ""
            renderer.console.print(f"  Alignment   : [dim_text]{align_sign}{align:.1f} ({align_label})[/dim_text]")
            renderer.console.print()

            # Stats
            renderer.console.print("  [system_msg][ STATS ][/system_msg]")
            s = bot.stats
            renderer.console.print(
                f"  STR {s.STR:3}   INT {s.INT:3}   AGI {s.AGI:3}   LCK {s.LCK:3}"
            )
            renderer.console.print(
                f"  VIT {s.VIT:3}   WIS {s.WIS:3}   END {s.END:3}"
            )
            renderer.console.print()

            # Inventory
            if bot.inventory:
                renderer.console.print("  [system_msg][ INVENTORY ][/system_msg]")
                for item_id in bot.inventory:
                    item = self.item_registry.get(item_id)
                    name = item.name if item else item_id
                    color = RARITY_COLORS.get(item.rarity.value, "white") if item else "white"
                    renderer.console.print(f"    [{color}]{name}[/{color}]")
            else:
                renderer.console.print("  [dim_text]Inventory: empty[/dim_text]")
            renderer.console.print()

            # Memory
            if bot.memory:
                renderer.console.print("  [system_msg][ RECENT MEMORY ][/system_msg]")
                for mem in bot.memory[-5:]:
                    renderer.console.print(f"    [dim_text]• {mem}[/dim_text]")
            renderer.console.print()

            sub = questionary.select(
                f"{bot.name}:",
                choices=["Set Goal", "Move Zone", "← Back"]
            ).ask()

            if sub == "Set Goal":
                new_goal = questionary.text("New goal:").ask()
                if new_goal:
                    bot.current_goal = new_goal.strip()
                    renderer.print_success(f"{bot.name}'s goal set to: {bot.current_goal}")
                    renderer.prompt_any_key()

            elif sub == "Move Zone":
                new_zone = questionary.text("Zone ID:").ask()
                if new_zone:
                    bot.current_zone_id = new_zone.strip()
                    renderer.print_success(f"{bot.name} moved to: {bot.current_zone_id}")
                    renderer.prompt_any_key()

    def _admin_panel(self) -> None:
        """Admin panel — feature flags, AI stats, debug tools."""
        while True:
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ ADMIN PANEL ][/system_msg]\n")

            bg_status = "ON" if self._bg_generator and self._bg_generator._running else "OFF"
            ai_status = "online" if self.ai_generator else "offline"
            director_status = "ON" if self._world_director else "OFF"
            renderer.console.print(f"  Director AI      : {director_status}")

            bot_count = self._bot_manager.active_count() if self._bot_manager else 0
            choices = [
                f"AI Token Usage  (AI: {ai_status})",
                f"Toggle Feature Flags",
                f"Background Generator: {bg_status}",
                f"Active Bots / Players  ({bot_count} bots · 1 player)",
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

            elif action.startswith("Active Bots"):
                self._admin_bots_panel()

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
                from ui.panels import _stat_bracket, RARITY_COLORS
                bracket = _stat_bracket(item.stats_bonus) if item.stats_bonus else ""
                qty_str = f" x{slot.quantity}" if slot.quantity > 1 else ""
                label = f"{display}{bracket}{qty_str}"
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
                sub_choices.append("Inspect")
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
                from ui.panels import RARITY_COLORS, _stat_bracket
                color = RARITY_COLORS.get(item.rarity.value, "white")
                renderer.console.print(f"\n  [{color}]{item.name}[/{color}]  [{color}]{item.rarity.value}[/{color}]")
                renderer.console.print(f"  [scene_text]{item.description}[/scene_text]")
                if item.flavor_text:
                    renderer.console.print(f'  [italic dim_text]"{item.flavor_text}"[/italic dim_text]')
                if item.backstory:
                    renderer.console.print(f'\n  [dim_text]{item.backstory}[/dim_text]')
                if item.stats_bonus:
                    bracket = _stat_bracket(item.stats_bonus)
                    if bracket:
                        renderer.console.print(f"  [dim_text]Bonuses:{bracket}[/dim_text]")
                renderer.console.print()
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

        for result in results:
            try:
                rtype = result.get("type")

                # ── Quests ────────────────────────────────────────────────────
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
                        renderer.console.print(
                            f"\n  [system_msg][ NEW QUEST AVAILABLE ][/system_msg]  "
                            f"[scene_text]{template.title}[/scene_text]"
                        )

                # ── Zone narrative ────────────────────────────────────────────
                elif rtype == "narrative":
                    zone_id = result.get("zone_id", "")
                    text    = result.get("text", "")
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

                # ── NPC branch ────────────────────────────────────────────────
                elif rtype == "npc_branch" and feature("npc_system") and self.npc_registry:
                    npc_id = result.get("npc_id", "")
                    node   = result.get("node", {})
                    if npc_id and node:
                        npc = self.npc_registry.get(npc_id)
                        if npc and hasattr(npc, "dialogue_nodes") and node.get("node_id"):
                            from entities.npc import NPCDialogueNode
                            try:
                                npc.dialogue_nodes[node["node_id"]] = NPCDialogueNode.model_validate(node)
                            except Exception:
                                pass
                        if feature("world_db") and self.state.world_db:
                            self.state.world_db.store_bg_content(
                                "npc_branch", npc_id, node,
                                generated_turn=self.state.player.turn_count,
                            )

                # ── Bot action ────────────────────────────────────────────────
                elif rtype == "bot_action" and feature("bot_system") and self._bot_manager:
                    bot_id = result.get("bot_id", "")
                    action = result.get("action", "")
                    target = result.get("target", "")
                    bot    = self._bot_manager.get(bot_id)
                    if bot and action:
                        event_text: str | None = None
                        if action == "move_zone":
                            from systems.world_zones import is_adjacent, get_connected
                            if is_adjacent(bot.current_zone_id, target):
                                event_text = (
                                    f"{bot.name} was spotted traveling toward "
                                    f"{target.replace('_', ' ').title()}."
                                )
                                bot.current_zone_id = target
                            else:
                                adj = get_connected(bot.current_zone_id)
                                if adj:
                                    bot.current_zone_id = adj[0]
                        elif action == "trade":
                            cost = min(50, bot.gold)
                            bot.gold -= cost
                            bot.memory.append(
                                f"Traded at {target} for {cost}g"
                                f" (turn {self.state.player.turn_count})"
                            )
                            event_text = (
                                f"{bot.name} completed a trade deal in "
                                f"{target.replace('_', ' ').title()}."
                            )
                        elif action == "rest":
                            bot.current_goal = "idle"
                        elif action == "craft":
                            bot.memory.append(
                                f"Crafted at {target}"
                                f" (turn {self.state.player.turn_count})"
                            )
                            event_text = (
                                f"{bot.name} was seen working at a crafting bench in "
                                f"{bot.current_zone_id.replace('_', ' ').title()}."
                            )
                        elif action == "talk_npc":
                            bot.memory.append(
                                f"Spoke with {target}"
                                f" (turn {self.state.player.turn_count})"
                            )
                            event_text = (
                                f"{bot.name} was overheard talking to "
                                f"{target.replace('_', ' ').title()} in "
                                f"{bot.current_zone_id.replace('_', ' ').title()}."
                            )
                        # Goal cycling every 10 turns of inactivity
                        if self.state.player.turn_count - bot.turn_last_acted >= 10:
                            _advance_bot_goal(bot)
                        bot.turn_last_acted = self.state.player.turn_count
                        if feature("world_db") and self.state.world_db:
                            self._bot_manager.save_to_db(self.state.world_db)
                            if event_text:
                                self.state.world_db.store_world_event(
                                    event_type="area_activity",
                                    event_text=event_text,
                                    zone_id=bot.current_zone_id,
                                    title="",
                                    npc_hint=bot.bot_id,
                                    generated_turn=self.state.player.turn_count,
                                )

                # ── Guild tick results ────────────────────────────────────────
                elif rtype == "guild_tick_results":
                    for r in result.get("results", []):
                        if r.success and r.narrative:
                            renderer.console.print(
                                f"  [dim_text][ {r.narrative} ][/dim_text]"
                            )

                # ── Director analysis result (silent — targets already queued) ─
                elif rtype == "director_fired":
                    logger.debug(
                        f"Director analysis integrated: "
                        f"{result.get('target_count', 0)} target(s) queued"
                    )

                # ── World events, rumors, lore, area activity ─────────────────
                elif rtype in ("world_event", "rumor", "lore_entry", "area_activity"):
                    event_text = result.get("event_text", "").strip()
                    r_zone_id  = result.get("zone_id", "")
                    title      = result.get("title", "")
                    npc_hint   = result.get("npc_hint", "")
                    if not event_text:
                        continue

                    # Persist to world_db
                    if feature("world_db") and self.state.world_db:
                        self.state.world_db.store_world_event(
                            event_type=rtype,
                            event_text=event_text,
                            zone_id=r_zone_id or self.state.current_scene_id,
                            title=title,
                            npc_hint=npc_hint,
                            generated_turn=self.state.player.turn_count,
                        )

                    # Visual feedback based on type
                    if rtype == "world_event":
                        renderer.console.print(
                            f"\n  [system_msg][ WORLD ][/system_msg]  [scene_text]{event_text}[/scene_text]"
                        )
                    elif rtype == "rumor":
                        renderer.console.print(
                            f"\n  [dim_text][ RUMOUR ][/dim_text]  [italic scene_text]{event_text}[/italic scene_text]"
                        )
                    elif rtype == "lore_entry":
                        renderer.console.print(
                            f"\n  [gold][ LORE ][/gold]  [scene_text]{event_text}[/scene_text]"
                        )
                    elif rtype == "area_activity":
                        renderer.console.print(
                            f"\n  [dim_text][ {(r_zone_id or 'nearby').upper()} ][/dim_text]  "
                            f"[scene_text]{event_text}[/scene_text]"
                        )

            except Exception as exc:
                logger.warning(f"Content integration error: {exc}")

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

    def _lore_log(self) -> None:
        """Browse AI-generated world events, rumors, and lore entries."""
        renderer.clear()
        renderer.print_title()
        renderer.console.print(
            "  [system_msg][ WORLD LOG ][/system_msg]  "
            "[dim_text]AI-generated world events, rumours, and lore[/dim_text]\n"
        )

        if not feature("world_db") or not self.state.world_db:
            renderer.console.print("  [dim_text]World database not available.[/dim_text]")
            renderer.prompt_any_key()
            return

        events = self.state.world_db.get_recent_events(limit=30)
        if not events:
            renderer.console.print(
                "  [dim_text]The world is still waking up. No events recorded yet.[/dim_text]\n"
                "  [dim_text](Background generation fires after your first action.)[/dim_text]"
            )
            renderer.prompt_any_key()
            return

        type_icons = {
            "world_event":   "[system_msg]WORLD[/system_msg]",
            "rumor":         "[dim_text]RUMOUR[/dim_text]",
            "lore_entry":    "[gold]LORE[/gold]",
            "area_activity": "[dim_text]AREA[/dim_text]",
            "quest":         "[system_msg]QUEST[/system_msg]",
            "narrative":     "[dim_text]NARRATIVE[/dim_text]",
            "npc_branch":    "[dim_text]NPC[/dim_text]",
        }

        for ev in reversed(events):
            icon     = type_icons.get(ev.get("event_type", ""), "[dim_text]EVENT[/dim_text]")
            turn     = ev.get("generated_turn", 0)
            text     = ev.get("event_text", "") or ev.get("definition", "")[:80]
            zone     = ev.get("zone_id", "")
            zone_str = f" [{zone}]" if zone else ""
            renderer.console.print(
                f"  {icon}  [dim_text]T{turn}{zone_str}[/dim_text]  [scene_text]{text}[/scene_text]"
            )
        renderer.console.print()
        renderer.prompt_any_key()

    def _save_prompt(self) -> None:
        slot = questionary.text("Save slot name:", default=self.state.player.name.lower().replace(" ", "_")).ask()
        if slot:
            path = save_game(self.state, slot, SAVES_DIR)
            logger.info(
                "Game saved: slot=%s player=%s level=%d scene=%s turn=%d",
                slot, self.state.player.name, self.state.player.level,
                self.state.current_scene_id, self.state.player.turn_count,
            )
            renderer.print_success(f"Game saved to {path.name}")
            time.sleep(0.5)
