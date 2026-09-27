from __future__ import annotations

import logging
import uuid as _uuid
from typing import TYPE_CHECKING

import questionary

from config import AI_QUEST_DISPOSITION_MIN, DATA_DIR, feature
from core.event_bus import bus, Event
from ui import renderer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DialogueHandlerMixin:

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

            options = list(resolved.options)

            # Inject dynamic quest-seed options at the disposition-hook root only
            # (avoid duplicating them on every sub-node of the conversation).
            seed_pairs: list[tuple] = []  # list[(SceneOption, NPCQuestSeed | None)]
            if (
                feature("quest_system")
                and self.quest_registry is not None
                and current_node == npc_system.get_disposition_hook(npc, disposition)
            ):
                seed_pairs = self._build_quest_seed_options(npc, disposition)
                options.extend(opt for opt, _ in seed_pairs)

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

            # ── Synthetic quest-seed option ───────────────────────────────────
            if chosen_opt_scene.option_id.startswith("__qseed__"):
                key = chosen_opt_scene.option_id[len("__qseed__"):]
                seed = next(
                    (s for opt, s in seed_pairs if opt.option_id == chosen_opt_scene.option_id),
                    None,
                )
                self._handle_quest_seed_chosen(seed, npc, key)
                bus.flush()
                # Stay on the same node; the offer is a side-effect, not navigation.
                continue

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
                        if self._ai_online():
                            with renderer.show_ai_thinking_spinner(f"{npc.name} considers..."):
                                ai_comment = self.ai_service.generate_shop_refusal(
                                    npc_name=npc.name,
                                    npc_role=npc.role,
                                    price_text=_fc(buy_price),
                                    gold_text=_fc(self.state.player.gold),
                                )
                            if ai_comment:
                                broke_comment = ai_comment
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

    # ── Quest-seed dialogue injection ─────────────────────────────────────────

    def _build_quest_seed_options(self, npc, disposition: float) -> list[tuple]:
        """Thin wrapper that hands NPC + state to the pure builder in npc_system."""
        from systems import npc_system
        return npc_system.build_quest_seed_options(
            npc=npc,
            state=self.state,
            quest_registry=self.quest_registry,
            disposition=disposition,
            ai_enabled=self._ai_online(),
            ai_quest_disposition_min=AI_QUEST_DISPOSITION_MIN,
        )

    def _handle_quest_seed_chosen(self, seed, npc, key: str) -> None:
        """Process the player choosing a synthetic quest-seed option."""
        from systems import quest_system as qs

        # Implicit AI offer (empty seeds + high disposition)
        if seed is None and key == "ai_implicit":
            self._offer_ai_quest(npc, implicit=True)
            return

        # Explicit "ai_dynamic" seed
        if seed is not None and seed.quest_template_id == "ai_dynamic":
            self._offer_ai_quest(npc, implicit=False, seed=seed)
            return

        # Concrete pre-written quest template
        if seed is None:
            return
        instance_id = qs.start_quest(
            seed.quest_template_id, npc.npc_id, self.state, self.quest_registry,
        )
        if instance_id:
            template = self.quest_registry.get(seed.quest_template_id)
            title = template.title if template else seed.quest_template_id
            renderer.print_npc_response(npc.name, f"Good. Here's what I need: {title}.")
            if seed.already_given_flag:
                self.state.player.set_flag(seed.already_given_flag)
        else:
            renderer.print_npc_response(npc.name, "Hm — looks like that's already on your list.")
        renderer.prompt_any_key()

    def _offer_ai_quest(self, npc, implicit: bool, seed=None) -> None:
        """Call the AI quest generator with a spinner; flag the NPC as offered."""
        from systems import quest_system as qs

        # Always set the offered flag, even on failure, so we don't spam attempts
        offered_flag = (
            seed.already_given_flag if (seed and seed.already_given_flag)
            else f"_ai_offered_{npc.npc_id}"
        )

        instance_id = None
        try:
            with renderer.show_ai_thinking_spinner(f"{npc.name} considers your offer..."):
                instance_id = qs.generate_ai_quest(
                    giver_npc_id=npc.npc_id,
                    npc_name=npc.name,
                    npc_role=npc.role,
                    state=self.state,
                    ai_service=getattr(self, "ai_service", None),
                )
        except Exception:
            logger.warning("AI quest offer failed", exc_info=True)

        self.state.player.set_flag(offered_flag)

        if instance_id:
            # Pull the freshly stored AI quest title for the in-character reply
            title = "a small matter"
            if self.state.world_db:
                ai_def = self.state.world_db.get_ai_quest_definition(instance_id)
                if ai_def:
                    title = ai_def.get("title", title)
            renderer.print_npc_response(npc.name, f"Aye — there is something. {title}.")
        else:
            renderer.print_npc_response(
                npc.name,
                "Hmph. Nothing comes to mind right now. Try again later.",
            )
        renderer.prompt_any_key()

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
        if self._ai_online():
            renderer.console.print("  [dim_text]Consulting the System...[/dim_text]")
            template = self.ai_service.generate_guild_template(
                name=name,
                archetype=archetype,
                zone_id=intent.zone_id,
                founding_reason=reason,
                seed_traits=[],
            )

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
                logger.warning("Failed to persist guild template to disk", exc_info=True)
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
