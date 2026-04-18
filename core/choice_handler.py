from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import questionary

from config import feature
from core.event_bus import bus, Event
from systems import class_system
from scenes.scene_base import SceneOption
from ui import renderer
from utils.logging_setup import log_player_error

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ChoiceHandlerMixin:

    def _handle_choice(self, option: SceneOption) -> None:
        player = self.state.player
        scene = self.scene_registry.get(self.state.current_scene_id)

        # Record as unexpected if flagged
        if not option.expected:
            key = f"unexpected:{self.state.current_scene_id}:{option.option_id}"
            player.record_choice(key)

        # Process triggers
        if scene:
            try:
                scene.process_triggers(option.triggers, self.state)
            except Exception as exc:
                log_player_error(
                    "trigger_crash",
                    exc=exc,
                    player=player,
                    scene_id=self.state.current_scene_id,
                    node_id=self.state.current_node_id,
                    option_id=option.option_id,
                    turn=player.turn_count,
                    extra={"triggers": str(option.triggers)},
                )
                logger.error("Trigger processing failed: %s", exc, exc_info=True)

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
            elif self.ai_generator:
                # Follow-up options have no pre-written narrative — generate one on the spot
                narrative_text = self._generate_action_narrative(option.label)
                if narrative_text:
                    renderer.print_divider()
                    renderer.print_scene_text([f"» {narrative_text}"])
                    renderer.print_divider()
                else:
                    renderer.print_system_message("Action taken.", style="dim_text")
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

    def _generate_action_narrative(self, action_label: str) -> str:
        """
        Generate a single-sentence outcome for a follow-up AI option that has no narrative.
        Uses the fast client with a tiny token budget for near-instant response.
        Returns empty string on failure.
        """
        try:
            scene = self.scene_registry.get(self.state.current_scene_id)
            scene_title = scene.title if scene else self.state.current_scene_id
            node = scene.get_node(self.state.current_node_id) if scene else {}
            scene_text = node.get("text", "")[:150]
            prompt = (
                f'Scene: {scene_title}. {scene_text}\n'
                f'Player action: "{action_label}"\n'
                'Describe the outcome in one vivid sentence (max 25 words). '
                'Return ONLY a JSON object: {"narrative": "..."}'
            )
            with renderer.show_ai_thinking_spinner(""):
                raw = self.ai_generator.fast_client.generate_json(
                    prompt=prompt,
                    system_prompt="You write one-sentence action outcomes for a fantasy RPG. Return only valid JSON.",
                    temperature=0.8,
                    num_predict=80,
                    max_retries=1,
                )
            return str(raw.get("narrative", "")).strip()
        except Exception as e:
            logger.warning("Action narrative generation failed: %s", e)
            return ""

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
                result = self.ai_generator.fast_client.generate_json(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0.75,
                    num_predict=200,
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
