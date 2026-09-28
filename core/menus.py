"""
GameMenusMixin — extracted menu methods from GameEngine.

All methods here are pure UI — they read/write self.state and registries but
do not drive the main game loop or handle combat/dialogue. Extracted from
core/game_engine.py to keep that file manageable.

Usage:
    class GameEngine(GameMenusMixin):
        ...
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import questionary

from config import DATA_DIR, SAVES_DIR, feature
from core.event_bus import Event, bus
from persistence.save_manager import save_game
from ui import renderer

import time as _time

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GameMenusMixin:
    """Mixin providing UI menu methods for GameEngine."""

    # ── Skills menu ───────────────────────────────────────────────────────────

    def _skills_menu(self) -> None:
        """Show all learned skills grouped by type with full detail view.

        Players with the Inspect utility passive AND a running AIService also
        get an "Inspect an unknown skill" entry — they describe / name a
        skill they've heard rumour of, and the AI generates a full
        Skill in their tier using ``source="inspect"`` and
        ``has_inspect=True`` (rich, technically-precise description).
        """
        from ui.panels import RARITY_COLORS
        player = self.state.player

        # Detect Inspect passive via the same utility_tags pipeline combat uses.
        inspect_available = False
        ai_service = getattr(self, "ai_service", None)
        if ai_service is not None and getattr(ai_service, "is_available", False):
            try:
                from systems.passive_system import get_passive_modifiers
                mods = get_passive_modifiers(player, self.skill_registry)
                inspect_available = "inspect" in mods.utility_tags
            except Exception:
                inspect_available = False

        if not player.skills and not inspect_available:
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
        INSPECT_CHOICE = "✦ Inspect an unknown skill (System-Awakened)"
        skill_choices: list[str] = []
        skill_map: dict[str, object] = {}
        # Show stat-points spend option whenever the player has any unspent.
        # This used to only fire post-combat-victory, so players who leveled
        # via quest XP would accumulate stat_points indefinitely.
        stat_pts = player.stat_points
        STATS_CHOICE = (
            f"⚡ Spend stat points  ({stat_pts} pending)"
            if stat_pts > 0 else None
        )
        if STATS_CHOICE:
            skill_choices.append(STATS_CHOICE)
        if inspect_available:
            skill_choices.append(INSPECT_CHOICE)
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

            # ── Stat-points spend flow ───────────────────────────────────
            if STATS_CHOICE and chosen == STATS_CHOICE:
                self._spend_stat_points()
                return self._skills_menu()

            # ── Inspect flow: ask the AI to materialise a new skill ──────
            if chosen == INSPECT_CHOICE:
                self._inspect_unknown_skill()
                # Rebuild the choice list so the new skill shows up
                return self._skills_menu()

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
            # Show level + use-count for skills with growth state
            cur_lvl = self.state.player.skill_levels.get(skill.skill_id, 1)
            uses = self.state.player.skill_uses.get(skill.skill_id, 0)
            if uses > 0 or cur_lvl > 1:
                renderer.console.print(
                    f"  Mastery : [system_msg]Lvl {cur_lvl}/{skill.max_level}[/system_msg]  "
                    f"[dim_text]({uses} uses in combat)[/dim_text]"
                )
            renderer.console.print()

            # Inspect existing skill — if player has Inspect passive and AI
            # is online, offer to re-render the description with mechanical
            # detail exposed (numbers, scaling, cooldown breakdown). Doesn't
            # change the skill's effects, only refreshes the description.
            if inspect_available:
                action = questionary.select(
                    "Action:",
                    choices=[
                        "← Back",
                        "✦ Inspect mechanics (uses your Inspect passive)",
                    ],
                ).ask()
                if action and action.startswith("✦"):
                    self._inspect_existing_skill(skill)
            else:
                renderer.prompt_any_key()

    def _inspect_existing_skill(self, skill) -> None:
        """Show a richer, mechanically-precise description of a skill the
        player already owns. Doesn't change the skill — just renders an
        Inspect-flavoured tooltip computed locally from the skill's effects
        plus the player's current stats. No AI call required.
        """
        from systems.skill_system import calculate_skill_damage
        renderer.clear()
        renderer.print_title()
        renderer.console.print(
            f"\n  [system_msg][ INSPECT — {skill.name.upper()} ][/system_msg]\n"
        )

        player = self.state.player
        cur_lvl = player.skill_levels.get(skill.skill_id, 1)
        uses    = player.skill_uses.get(skill.skill_id, 0)
        to_next_lvl = "MASTERED" if cur_lvl >= skill.max_level else (
            f"{5 - (uses % 5)} more uses to Lv {cur_lvl + 1}"
        )

        renderer.console.print(f"  [dim_text]{skill.description}[/dim_text]\n")
        renderer.console.print(f"  Type           : {skill.skill_type.value}")
        renderer.console.print(f"  Rarity         : {skill.rarity.value}")
        if skill.mp_cost > 0:
            mp_pct = (skill.mp_cost / player.max_mp * 100) if player.max_mp else 0
            renderer.console.print(
                f"  MP cost        : {skill.mp_cost}  [dim_text]({mp_pct:.0f}% of your pool)[/dim_text]"
            )
        if skill.cooldown_turns > 0:
            renderer.console.print(f"  Cooldown       : {skill.cooldown_turns} combat turns")
        renderer.console.print(f"  Mastery        : Lv {cur_lvl}/{skill.max_level}  ({uses} uses, {to_next_lvl})")
        if skill.effects:
            renderer.console.print("  Live values    :")
            for eff in skill.effects:
                stat = eff.scaling_stat or "—"
                stat_val = getattr(player.stats, eff.scaling_stat, 0) if eff.scaling_stat else 0
                # Show what this effect would actually compute to right now.
                raw = eff.base_value + stat_val * eff.scaling_coefficient
                # Apply skill-level scaling the same way calculate_skill_damage does
                lvl_steps = max(0, cur_lvl - 1)
                final = eff.base_value + lvl_steps * 1.0 + stat_val * eff.scaling_coefficient * (1.0 + lvl_steps * 0.10)
                renderer.console.print(
                    f"    [dim_text]{eff.effect_type.value.upper():<8} "
                    f"base {eff.base_value} + {stat}({stat_val})×{eff.scaling_coefficient} "
                    f"= [system_msg]{int(max(1, final))}[/system_msg][/dim_text]"
                )
            # Sanity check via the engine's own formula
            real_dmg = calculate_skill_damage(skill, player)
            renderer.console.print(
                f"\n  [bright_yellow]System calculation: this skill would output {real_dmg} right now.[/bright_yellow]"
            )
        renderer.console.print()
        renderer.prompt_any_key()

    def _spend_stat_points(self) -> None:
        """Interactive menu to spend pending stat_points.

        Previously stat points only had a UI in the post-combat-victory
        flow — players who leveled up via quest XP (no combat) had their
        points pile up silently. This menu makes them spendable anywhere
        the player opens the [K] skills menu.
        """
        from systems import level_system

        player = self.state.player
        while player.stat_points > 0:
            renderer.clear()
            renderer.print_title()
            renderer.console.print(
                f"\n  [system_msg][ STAT POINTS — {player.stat_points} TO SPEND ][/system_msg]\n"
            )
            s = player.stats
            renderer.console.print(
                f"  STR {s.STR}   INT {s.INT}   AGI {s.AGI}   LCK {s.LCK}\n"
                f"  VIT {s.VIT}   WIS {s.WIS}   END {s.END}\n"
                f"\n  HP {player.current_hp}/{player.max_hp}    "
                f"MP {player.current_mp}/{player.max_mp}\n"
            )
            choices = [
                f"STR {s.STR}  (+1 melee damage)",
                f"INT {s.INT}  (+1 spell damage)",
                f"AGI {s.AGI}  (+dodge, +crit chance)",
                f"LCK {s.LCK}  (+crit chance, +loot rolls)",
                f"VIT {s.VIT}  (+5 max HP)",
                f"WIS {s.WIS}  (+spell scaling, +heal scaling)",
                f"END {s.END}  (+3 max HP, +damage reduction)",
                "← Done",
            ]
            choice = questionary.select(
                f"Spend 1 point ({player.stat_points} remaining):",
                choices=choices,
            ).ask()
            if choice is None or choice == "← Done":
                break
            stat = choice.split()[0]
            ok_ = level_system.spend_stat_point(player, stat)
            if not ok_:
                renderer.print_error(f"Could not spend on {stat}.")
                renderer.prompt_any_key()

        if player.stat_points == 0:
            renderer.print_system_message(
                "All stat points allocated.", style="success",
            )
            import time as _t
            _t.sleep(0.6)

    def _inspect_unknown_skill(self) -> None:
        """Player-driven AI skill generation via the Inspect passive.

        The player describes a skill they've heard rumour of (a name, a
        short phrase). The AI generates a fully-mechanically-defined Skill
        themed to their class + level, with the rich Inspect-tooltip
        description style. The result is registered + added to the player's
        skill list — they now know it.
        """
        from ui.panels import RARITY_COLORS

        ai_service = getattr(self, "ai_service", None)
        if ai_service is None or not getattr(ai_service, "is_available", False):
            renderer.print_system_message(
                "The System is silent. (AI offline — Inspect cannot reach across the silence.)",
                style="dim_text",
            )
            renderer.prompt_any_key()
            return

        renderer.clear()
        renderer.print_title()
        renderer.console.print(
            "\n  [system_msg][ INSPECT — UNKNOWN SKILL ][/system_msg]\n"
        )
        renderer.console.print(
            "  [dim_text]Name or describe the ability you've witnessed / heard whispers of.\n"
            "  The System will reveal what it actually is.[/dim_text]\n"
        )
        hint = questionary.text(
            "What skill are you inspecting?",
            instruction="(e.g. 'Echoing Hammer Strike' or 'a stealth move that bends shadow')",
        ).ask()
        if not hint or not hint.strip():
            return

        # Build context so the LLM gets situational anchors. Scene context
        # helps it pick a thematically-coherent skill even when the hint
        # is vague.
        ctx = {}
        if getattr(self, "scene_registry", None) is not None and self.state:
            scene = self.scene_registry.get(self.state.current_scene_id)
            if scene is not None:
                ctx["scene_title"] = scene.title
                node = scene.get_node(self.state.current_node_id) if hasattr(scene, "get_node") else {}
                ctx["scene_text"] = (node or {}).get("text", "")[:240]

        skill = None
        try:
            with renderer.show_ai_thinking_spinner(f"INSPECTING: {hint[:40]}..."):
                skill = ai_service.generate_skill(
                    player=self.state.player,
                    name_hint=hint.strip(),
                    source="inspect",
                    context=ctx,
                    has_inspect=True,    # this whole flow IS the Inspect passive at work
                )
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Inspect generation crashed", exc_info=True)

        if skill is None:
            renderer.print_system_message(
                "The System could not resolve that ability. Try a clearer description.",
                style="dim_text",
            )
            renderer.prompt_any_key()
            return

        # Register + grant
        if self.skill_registry.get(skill.skill_id) is None:
            self.skill_registry.register(skill)
        if skill.skill_id not in self.state.player.skills:
            self.state.player.skills.append(skill.skill_id)
        bus.publish(Event("SKILL_ACQUIRED", {
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "rarity": skill.rarity.value,
        }))
        # Persist for save reload
        if feature("world_db") and self.state.world_db is not None:
            try:
                self.state.world_db.store_ai_skill(
                    skill_id=skill.skill_id,
                    definition=skill.model_dump(mode="json"),
                    source="inspect",
                    generated_turn=self.state.player.turn_count,
                )
            except Exception:
                pass

        # Show the result with the Inspect-rich detail
        renderer.clear()
        renderer.print_title()
        color = RARITY_COLORS.get(skill.rarity.value, "white")
        renderer.console.print(
            f"\n  [system_msg]✦ SYSTEM REVELATION ✦[/system_msg]\n"
            f"\n  [{color}]{skill.name}[/{color}]  [{color}]{skill.rarity.value}[/{color}]"
        )
        renderer.console.print(f"\n  [scene_text]{skill.description}[/scene_text]")
        if skill.flavor_text:
            renderer.console.print(f'  [italic dim_text]"{skill.flavor_text}"[/italic dim_text]')
        renderer.console.print()
        renderer.console.print(f"  Type    : [dim_text]{skill.skill_type.value}[/dim_text]")
        if skill.mp_cost > 0:
            renderer.console.print(f"  MP Cost : [mp]{skill.mp_cost}[/mp]")
        if skill.cooldown_turns > 0:
            renderer.console.print(f"  Cooldown: {skill.cooldown_turns} turns")
        if skill.effects:
            renderer.console.print("  Effects :")
            for eff in skill.effects:
                stat = eff.scaling_stat or "—"
                renderer.console.print(
                    f"    [dim_text]{eff.effect_type.value.upper()}  base={eff.base_value}  "
                    f"×{eff.scaling_coefficient} {stat}[/dim_text]"
                )
        renderer.console.print()
        renderer.print_system_message(
            f"Skill acquired: {skill.name}", style="success",
        )
        renderer.prompt_any_key()

    # ── Crafting menu ─────────────────────────────────────────────────────────

    def _craft_menu(self) -> None:
        """Alchemy / crafting menu — combine ingredients into items."""
        from systems.alchemy_system import craft_item
        recipes = self.recipes
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

    # ── Inventory menu ────────────────────────────────────────────────────────

    def _inventory_menu(self) -> None:
        """Interactive inventory and equipment management menu."""
        from systems.inventory_system import (
            equip_item, unequip_item, use_item,
            get_item_display_name, get_item_identify_hint,
        )
        from entities.enums import ItemType
        from core.event_bus import bus

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

    # ── Quest journal ─────────────────────────────────────────────────────────

    def _quests_menu(self) -> None:
        """
        Quest Journal — shows active quest objectives + completed quests.
        Drill into a quest to see full description, stage, rewards, turns elapsed.
        """
        if not feature("quest_system") or not self.quest_registry:
            renderer.print_system_message("Quest system is disabled.", style="dim_text")
            renderer.prompt_any_key()
            return

        from systems import quest_system as qs

        player = self.state.player

        while True:
            renderer.clear()
            renderer.print_title()
            renderer.print_status_bar(player, self.item_registry)
            renderer.print_divider()
            renderer.console.print("  [system_msg][ QUEST JOURNAL ][/system_msg]")
            renderer.console.print()

            active = qs.get_active_quest_summaries(self.state, self.quest_registry)
            completed_ids = list(player.completed_quest_ids)

            if not active and not completed_ids:
                renderer.console.print("  [dim_text]No quests yet. Talk to NPCs to find work.[/dim_text]")
                renderer.console.print()
                renderer.prompt_any_key()
                return

            choices: list[str] = []
            label_to_instance: dict[str, str] = {}

            if active:
                renderer.console.print("  [subtitle]Active[/subtitle]")
                for s in active:
                    label = f"  ● {s['title']}  [dim_text]— {s['objective']}[/dim_text]"
                    renderer.console.print(label)
                    pick = f"● {s['title']}"
                    choices.append(pick)
                    label_to_instance[pick] = s["instance_id"]
                renderer.console.print()

            if completed_ids:
                renderer.console.print("  [subtitle]Completed[/subtitle]")
                completed_count = 0
                for instance_id in completed_ids[-5:]:  # show last 5
                    row = self.state.world_db.get_quest(instance_id) if self.state.world_db else None
                    template = self.quest_registry.get(row["template_id"]) if row else None
                    if template:
                        renderer.console.print(f"  [dim_text]✓ {template.title}[/dim_text]")
                        completed_count += 1
                if completed_count == 0:
                    renderer.console.print("  [dim_text](none readable)[/dim_text]")
                renderer.console.print()

            choices.append("← Close")
            answer = questionary.select("Inspect quest:", choices=choices).ask()
            if answer is None or answer == "← Close":
                break

            instance_id = label_to_instance.get(answer)
            if instance_id:
                self._quest_detail(instance_id)

    def _quest_detail(self, instance_id: str) -> None:
        """Show full quest detail — description, current objective, rewards, NPC, turns elapsed."""
        if not self.state.world_db:
            return
        row = self.state.world_db.get_quest(instance_id)
        if not row:
            renderer.print_system_message("Quest not found.", style="system_warning")
            renderer.prompt_any_key()
            return
        template = self.quest_registry.get(row["template_id"]) if self.quest_registry else None
        if not template:
            renderer.print_system_message("Quest template missing.", style="system_warning")
            renderer.prompt_any_key()
            return

        stage = template.get_stage(row["current_state"])
        turns_active = self.state.turn_number - row.get("accepted_turn", 0)

        renderer.clear()
        renderer.print_title()
        renderer.print_divider()
        renderer.console.print(f"  [title]{template.title}[/title]")
        renderer.console.print()
        if template.description:
            renderer.console.print(f"  [scene_text]{template.description}[/scene_text]")
            renderer.console.print()

        if stage:
            renderer.console.print(f"  [subtitle]Current objective[/subtitle]")
            renderer.console.print(f"    » {stage.objective_text}")
            renderer.console.print()

        renderer.console.print(f"  [subtitle]Status[/subtitle]")
        renderer.console.print(f"    Stage      : [highlight]{row['current_state']}[/highlight]")
        renderer.console.print(f"    Turns open : [dim_text]{turns_active}[/dim_text]")
        if template.giver_npc_id:
            renderer.console.print(f"    Given by   : [dim_text]{template.giver_npc_id}[/dim_text]")
        if template.time_limit_turns:
            remaining = max(0, template.time_limit_turns - turns_active)
            renderer.console.print(f"    Time limit : [system_warning]{remaining} turns left[/system_warning]")
        renderer.console.print()

        # Rewards summary
        reward_bits = []
        if template.reward_gold:    reward_bits.append(f"[gold]{template.reward_gold} gold[/gold]")
        if template.reward_xp:      reward_bits.append(f"[xp]{template.reward_xp} xp[/xp]")
        if template.reward_items:   reward_bits.append(f"{len(template.reward_items)} item(s)")
        if template.reward_alignment: reward_bits.append(f"alignment {template.reward_alignment:+.1f}")
        if reward_bits:
            renderer.console.print(f"  [subtitle]Rewards on completion[/subtitle]")
            renderer.console.print("    " + "   ".join(reward_bits))
            renderer.console.print()

        renderer.prompt_any_key()

    # ── Admin bots panel ──────────────────────────────────────────────────────

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
            if align >= 50:    align_label = "Virtuous"
            elif align >= 20:  align_label = "Good-Natured"
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

    # ── Admin panel ───────────────────────────────────────────────────────────

    def _admin_panel(self) -> None:
        """Admin panel — feature flags, AI stats, debug tools."""
        while True:
            renderer.clear()
            renderer.print_title()
            renderer.console.print("\n  [system_msg][ ADMIN PANEL ][/system_msg]\n")

            bg_status = "ON" if self._bg_generator and self._bg_generator._running else "OFF"
            ai_status = "online" if self._ai_online() else "offline"
            director_status = "ON" if self._world_director else "OFF"
            renderer.console.print(f"  Director AI      : {director_status}")

            bot_count = self._bot_manager.active_count() if self._bot_manager else 0
            choices = [
                f"AI Token Usage  (AI: {ai_status})",
                "World Growth log",
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
                if not self._ai_online():
                    renderer.print_system_message("AI system is offline.", style="dim_text")
                else:
                    # One entry per distinct Ollama client (primary, plus fast
                    # when a separate fast model is configured).
                    usage = self.ai_service.token_usage()

                    renderer.console.print()
                    renderer.console.print("  [system_msg][ OLLAMA TOKEN USAGE — THIS SESSION ][/system_msg]")

                    grand_calls = 0
                    grand_prompt = 0
                    grand_gen = 0
                    for role, model_name, stats in usage:
                        renderer.console.print()
                        renderer.console.print(
                            f"  [subtitle]» {model_name}[/subtitle] [dim_text]({role})[/dim_text]"
                        )
                        renderer.console.print(f"    Calls made       : [gold]{stats['calls']}[/gold]")
                        renderer.console.print(f"    Prompt tokens    : [gold]{stats['prompt_tokens']:,}[/gold]")
                        renderer.console.print(f"    Generated tokens : [gold]{stats['generated_tokens']:,}[/gold]")
                        renderer.console.print(f"    Total tokens     : [gold]{stats['total_tokens']:,}[/gold]")
                        grand_calls  += stats['calls']
                        grand_prompt += stats['prompt_tokens']
                        grand_gen    += stats['generated_tokens']

                    if len(usage) > 1:
                        renderer.console.print()
                        renderer.console.print("  [system_msg]» combined[/system_msg]")
                        renderer.console.print(f"    Calls made       : [gold]{grand_calls}[/gold]")
                        renderer.console.print(f"    Prompt tokens    : [gold]{grand_prompt:,}[/gold]")
                        renderer.console.print(f"    Generated tokens : [gold]{grand_gen:,}[/gold]")
                        renderer.console.print(f"    Total tokens     : [gold]{grand_prompt + grand_gen:,}[/gold]")

                    renderer.console.print()
                    bg_q = self._bg_generator._task_queue.qsize() if self._bg_generator else 0
                    renderer.console.print(f"  BG tasks queued    : [dim_text]{bg_q}[/dim_text]")
                    renderer.console.print()
                renderer.prompt_any_key()

            elif action == "World Growth log":
                from core.world_growth_flow import world_growth_report
                renderer.console.print("\n  [system_msg][ WORLD GROWTH — THIS SAVE ][/system_msg]\n")
                for line in world_growth_report(self):
                    renderer.console.print(f"  [dim_text]{line}[/dim_text]")
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

    # ── Feature flag toggle ───────────────────────────────────────────────────

    def _admin_toggle_features(self) -> None:
        """Interactive feature flag toggle menu."""
        from config import FEATURES
        while True:
            renderer.clear()
            renderer.console.print("\n  [system_msg][ FEATURE FLAGS ][/system_msg]\n")
            flag_choices = []
            for flag, enabled in FEATURES.items():
                status = "ON " if enabled else "OFF"
                flag_choices.append(f"  [{status}]  {flag}")
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

    # ── Lore log ──────────────────────────────────────────────────────────────

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

    # ── Save prompt ───────────────────────────────────────────────────────────

    def _save_prompt(self) -> None:
        slot = questionary.text(
            "Save slot name:",
            default=self.state.player.name.lower().replace(" ", "_"),
        ).ask()
        if slot:
            import re
            slot = re.sub(r'[^a-z0-9_]', '', slot.lower().replace(" ", "_")) or "save"
            path = save_game(self.state, slot, SAVES_DIR)
            logger.info(
                "Game saved: slot=%s player=%s level=%d scene=%s turn=%d",
                slot, self.state.player.name, self.state.player.level,
                self.state.current_scene_id, self.state.player.turn_count,
            )
            renderer.print_success(f"Game saved to {path.name}")
            _time.sleep(0.5)
