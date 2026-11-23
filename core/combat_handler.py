from __future__ import annotations

import logging
import random
import time as _time
from typing import TYPE_CHECKING

from ui import prompt as uip
from config import feature
from core.event_bus import bus, Event
from systems import combat_system, lair_system, level_system
from ui import renderer
from entities.enums import SkillType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

COMPANION_DRAW_CHANCE = 0.3     # chance each enemy attacks your companion instead of you


def _enemy_bar(e) -> str:
    pct = e.current_hp / e.max_hp if e.max_hp > 0 else 0
    filled = int(pct * 10)
    bar = "█" * filled + "░" * (10 - filled)
    color = "hp_high" if pct > 0.6 else ("hp_mid" if pct > 0.3 else "hp_low")
    return f"[{color}]{bar}[/{color}] {e.current_hp}/{e.max_hp}"


def _skill_label(sid: str, player, skill_registry) -> str:
    sk = skill_registry.get(sid)
    if sk is None:
        return sid
    label = sk.name
    cd = player.skill_cooldowns.get(sid, 0)
    if cd > 0:
        label += f" [CD:{cd}]"
    elif sk.mp_cost > 0:
        label += f" [{sk.mp_cost} MP]"
    return label


_HEAL_TRIGGER_EVENTS = ("on_kill", "on_low_hp")


def _print_triggers(fired) -> None:
    """Log each TRIGGERED skill that fired (Arcane Strike, Last Stand, ...)."""
    for f in fired:
        if f.event in _HEAL_TRIGGER_EVENTS:
            renderer.print_combat_action(f"✦ {f.skill_name}", "triggers — restores HP", f.value, "success")
        else:
            renderer.print_combat_action(f"✦ {f.skill_name}", "triggers — bonus damage", f.value, "rare")


class CombatHandlerMixin:

    # ── Boss tactics (ai/decision) ────────────────────────────────────────────

    def _plan_tactics(self, enemies: list, player, player_statuses: dict):
        """Ask, in the background, which move each ability-using enemy makes
        this round. Returns a pending job or None (rules decide)."""
        from config import BOSS_TACTICS, DECISION_PROVIDER
        from systems import combat_effects as fx
        if not BOSS_TACTICS or DECISION_PROVIDER == "off" or not self._ai_online():
            return None
        asks, questions = [], []
        for e in enemies:
            if not e.is_alive or fx.heavy_due(e):
                continue
            opts = fx.tactic_options(e, enemies)
            if opts:
                q, labels = fx.tactic_question(e, player, player_statuses, opts)
                asks.append((e, opts))
                questions.append((q, labels))
        if not questions:
            return None
        future = self.ai_service.submit_decide_many_async(questions)
        return (future, asks) if future is not None else None

    def _tactic_for(self, job, enemy):
        """The chosen move for ``enemy``, if its answer came back in time."""
        from config import BOSS_TACTIC_WAIT
        if job is None:
            return None
        future, asks = job
        if not hasattr(future, "_sb_answers"):
            try:
                future._sb_answers = future.result(timeout=BOSS_TACTIC_WAIT)
            except Exception:
                future._sb_answers = []
        for (e, opts), d in zip(asks, future._sb_answers or []):
            if e is enemy and d is not None and 0 <= d.index < len(opts):
                return opts[d.index]
        return None

    def _run_combat(self, encounter_id: str, surprise: bool = False, enemies: list | None = None,
                    nonlethal: bool = False) -> str:
        """Fight ``encounter_id`` (or the given pre-spawned ``enemies``, e.g. a
        scaled lair boss). Returns "victory", "fled", "defeat" or "none".
        ``self._last_killing_blow`` is "magic" or "physical" afterwards."""
        from utils import session_log as _slog
        from ui import media as _media
        _media.on_combat_start(boss=any(getattr(e, "abilities", None) for e in (enemies or [])))
        _slog.event("combat_start", encounter=encounter_id, surprise=surprise,
                    enemies=[getattr(e, "name", e) for e in (enemies or [])] or None)
        if enemies is None:
            enemies = combat_system.spawn_encounter(encounter_id)
        self._last_killing_blow = "physical"
        if not enemies:
            return "none"

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
        _time.sleep(0.5)

        player = self.state.player
        # Skill cooldowns are PER-ENCOUNTER: reset everything at the start
        # of each combat so a 5-CD spell isn't "off cooldown" just because
        # the player took 5 menu actions in the village. Cooldowns now tick
        # per combat turn (see end of the loop below).
        from systems.skill_system import reset_cooldowns_for_combat
        reset_cooldowns_for_combat(player)
        from systems import combat_effects as fx
        player_statuses: dict = {}      # statuses end with the fight
        self._last_combat_rounds = 0
        turn = 0
        alive_enemies = [e for e in enemies if e.is_alive]

        # Surprise round: player gets one free attack before enemies can respond
        if surprise and alive_enemies:
            target = alive_enemies[0]
            fired = []
            dmg, is_crit = combat_system.player_attack(player, target, self.skill_registry, fired)
            crit_str = " CRITICALLY" if is_crit else ""
            renderer.print_combat_action(
                "SURPRISE!", f"You strike {target.name}{crit_str} before they react!", dmg, "system_warning"
            )
            _print_triggers(fired)
            if not target.is_alive:
                renderer.print_combat_action(target.name, "goes down before the fight begins!", style="success")
            alive_enemies = [e for e in enemies if e.is_alive]
            _time.sleep(0.4)

        while alive_enemies and player.current_hp > 0:
            turn += 1
            renderer.clear()
            renderer.print_title()
            renderer.print_status_bar(player, self.item_registry)
            renderer.print_divider()

            # Show all enemies with HP bars
            renderer.console.print(f"  [system_warning]⚔  TURN {turn}[/system_warning]")
            for i, enemy in enumerate(alive_enemies, 1):
                st = fx.status_label(enemy.statuses)
                renderer.console.print(
                    f"  [{i}] [damage]{enemy.name}[/damage]  {_enemy_bar(enemy)}  "
                    f"[dim_text]ATK {enemy.attack}  DEF {enemy.defense}{'  ' + st if st else ''}"
                    f"{'  ENRAGED' if enemy.enraged else ''}[/dim_text]"
                )
            if player_statuses:
                renderer.console.print(f"  [system_warning]You: {fx.status_label(player_statuses)}[/system_warning]")
            renderer.console.print()

            # Bosses think while the player does (one batched decision).
            tactics = self._plan_tactics(alive_enemies, player, player_statuses)

            # ── Choose action ─────────────────────────────────────────────────
            # Only include ACTIVE skills. PASSIVE skills contribute via
            # systems/passive_system (defense/attack bonuses, dodge, etc.).
            # TRIGGERED skills fire automatically from combat_system on the
            # right event (on_attack / on_hit / on_kill / on_low_hp) — showing them in the menu
            # would let the player double-fire them.
            skills_available = [
                sid for sid in player.skills
                if (sk := self.skill_registry.get(sid)) and sk.skill_type == SkillType.ACTIVE
            ]
            action_choices = ["⚔ Basic Attack"] + [_skill_label(sid, player, self.skill_registry) for sid in skills_available] + ["🏃 Flee"]
            if fx.consume_stun(player_statuses):
                renderer.print_combat_action("You", "are stunned and lose your turn!", style="system_warning")
                action = "__stunned__"
            else:
                action = uip.select("Your action:", choices=action_choices).ask()

                if action is None:
                    # Treat cancellation as re-prompt (loop continues naturally)
                    continue

                if action == "🏃 Flee":
                    if combat_system.try_flee(player, alive_enemies[0]):
                        renderer.print_scene_text(["You flee from combat!"])
                        renderer.prompt_any_key()
                        _media.on_combat_end("fled")
                        _slog.event("combat_end", encounter=encounter_id, result="fled", hp=self.state.player.current_hp)
                        return "fled"
                    else:
                        renderer.print_scene_text(["You failed to flee!"])

                else:
                    # ── Choose target (if more than one enemy alive) ──────────────
                    if len(alive_enemies) > 1 and action != "🏃 Flee":
                        target_choices = [
                            f"[{i}] {e.name}  ({e.current_hp}/{e.max_hp} HP)"
                            for i, e in enumerate(alive_enemies, 1)
                        ]
                        t_answer = uip.select("Target:", choices=target_choices).ask()
                        try:
                            t_idx = int(t_answer.split("]")[0].lstrip("[")) - 1
                            target = alive_enemies[t_idx]
                        except Exception:
                            target = alive_enemies[0]
                    else:
                        target = alive_enemies[0]

                    # ── Execute action ────────────────────────────────────────────
                    if action == "⚔ Basic Attack":
                        fired = []
                        dmg, is_crit = combat_system.player_attack(player, target, self.skill_registry, fired)
                        crit_str = " CRITICALLY" if is_crit else ""
                        renderer.print_combat_action(
                            "You", f"strike {target.name}{crit_str} for", dmg,
                            "critical" if is_crit else "damage",
                        )
                        _print_triggers(fired)
                        for eff, chance in fx.trigger_statuses(fired):
                            if target.is_alive and random.random() < chance and fx.inflict(target.statuses, eff, target.rank):
                                renderer.print_combat_action(target.name, f"is {eff}ed" if eff == "poison" else f"suffers {eff}", style="success")
                        renderer.console.print(
                            f"  [dim_text]→ {target.name}: {target.current_hp}/{target.max_hp} HP remaining[/dim_text]"
                        )
                        if not target.is_alive:
                            self._last_killing_blow = "physical"
                            renderer.print_combat_action(target.name, "is defeated!", style="success")

                    else:
                        # Skill / spell
                        skill_id = next(
                            (sid for sid in skills_available if _skill_label(sid, player, self.skill_registry) == action), None
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
                                if fx.is_defensive(skill):
                                    player.current_mp -= skill.mp_cost
                                    if skill.cooldown_turns > 0:
                                        player.skill_cooldowns[skill_id] = skill.cooldown_turns
                                    from systems.skill_system import record_skill_use
                                    record_skill_use(player, skill_id, self.skill_registry)
                                    renderer.print_scene_text([fx.apply_defensive(skill, player, player_statuses)])
                                elif is_heal:
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
                                        _st = fx.skill_status(skill)
                                        if _st and target.is_alive and random.random() < _st[1]:
                                            if fx.inflict(target.statuses, _st[0], target.rank):
                                                renderer.print_combat_action(target.name, f"is afflicted: {_st[0]}", style="success")
                                            else:
                                                renderer.print_combat_action(target.name, f"resists {_st[0]}", style="miss")
                                        renderer.console.print(
                                            f"  [dim_text]→ {target.name}: {target.current_hp}/{target.max_hp} HP remaining[/dim_text]"
                                        )
                                    else:
                                        renderer.print_scene_text(["Not enough MP!"])
                                    if not target.is_alive:
                                        self._last_killing_blow = lair_system.killing_blow_kind(skill)
                                        renderer.print_combat_action(target.name, "is defeated!", style="success")

            # ── Companion (a bot on this gate run) ────────────────────────────
            party = [c for c in (getattr(self, "_companions", None) or []) if c.hp > 1]
            for comp in party:
                if not any(e.is_alive for e in enemies):
                    break
                from systems.bot_brain import bot_attack
                foe = random.choice([e for e in enemies if e.is_alive])
                hit = max(1, round((bot_attack(comp) - foe.defense) * random.uniform(0.8, 1.2)))
                foe.current_hp = max(0, foe.current_hp - hit)
                renderer.print_combat_action(comp.name, f"strikes {foe.name} for", hit, "rare")
                if not foe.is_alive:
                    renderer.print_combat_action(foe.name, "is defeated!", style="success")
            alive_enemies = [e for e in enemies if e.is_alive]

            # ── Enemy turns ───────────────────────────────────────────────────
            for enemy in list(alive_enemies):
                party = [c for c in party if c.hp > 1]
                if (party and enemy.is_alive and not enemy.statuses.get("stun")
                        and random.random() < min(0.6, COMPANION_DRAW_CHANCE * len(party))):
                    from systems.bot_brain import bot_defense
                    comp = random.choice(party)
                    taken = max(1, enemy.attack - bot_defense(comp))
                    comp.hp = max(1, comp.hp - taken)
                    renderer.print_combat_action(enemy.name, f"turns on {comp.name} for", taken, "damage")
                    if comp.hp <= 1:
                        renderer.print_combat_action(comp.name, "is knocked down and drags themself out of the fight.",
                                                     style="system_warning")
                    continue
                if enemy.is_alive and player.current_hp > 0:
                    res = fx.enemy_turn(enemy, player, player_statuses, self.skill_registry, enemies,
                                        chosen=self._tactic_for(tactics, enemy))
                    if res.damage:
                        _media.sfx("enemy_hit")
                    for text, style in res.lines:
                        renderer.console.print(f"  [{style}]{text}[/{style}]")
                    _print_triggers(res.fired)
                    if res.damage:
                        renderer.console.print(
                            f"  [dim_text]→ Your HP: {player.current_hp}/{player.max_hp}[/dim_text]"
                        )

            # ── End of round: damage over time ────────────────────────────────
            for enemy in enemies:
                if enemy.is_alive:
                    for eff, dot in fx.tick_enemy(enemy):
                        renderer.print_combat_action(enemy.name, f"takes {eff} damage", dot, "success")
                    if not enemy.is_alive:
                        renderer.print_combat_action(enemy.name, "is defeated!", style="success")
            if player.current_hp > 0 and player_statuses:
                player.current_hp, hits = fx.tick(player_statuses, player.current_hp)
                for eff, dot in hits:
                    renderer.print_combat_action("You", f"take {eff} damage", dot, "damage")
            if player.current_hp > 0:
                combat_system.combat_mp_regen(player)
            self._last_combat_rounds = turn

            # End-of-round cooldown tick — F5: per combat turn, not per game-loop turn
            from systems.skill_system import tick_combat_cooldowns
            tick_combat_cooldowns(player)

            alive_enemies = [e for e in enemies if e.is_alive]
            _time.sleep(0.3)

        if player.current_hp <= 0 and nonlethal:
            # Wardens and arrests beat you down; they don't kill you.
            player.current_hp = 1
            renderer.print_system_message("YOU ARE BEATEN DOWN.", style="system_warning")
            _media.on_combat_end("defeat")
            _slog.event("combat_end", encounter=encounter_id, result="defeat", hp=1)
            return "defeat"
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
                    _media.system_voice("game_over")
                    renderer.print_scene_text(["The System erases your record. You are gone."])
                    renderer.prompt_any_key()
                    self._running = False
                else:
                    _media.system_voice("life_lost")
                    renderer.print_scene_text([
                        f"A life burns away. {player.lives_remaining} life{'s' if player.lives_remaining != 1 else ''} remaining.",
                        "You wake at your last safe point, battered but breathing.",
                    ])
                    renderer.prompt_any_key()
            else:
                renderer.print_scene_text(["The System records your defeat."])
                renderer.prompt_any_key()
                self._running = False
            _media.on_combat_end("defeat")
            _slog.event("combat_end", encounter=encounter_id, result="defeat", hp=self.state.player.current_hp)
            return "defeat"

        # Victory
        total_xp = sum(e.xp_reward for e in enemies)
        total_gold = sum(e.gold_reward for e in enemies)
        # Pass registries so add_experience can auto-grant the next
        # learnable_skill from the player's class on level-up (F9).
        leveled_up = level_system.add_experience(
            player, total_xp,
            class_registry=self.class_registry,
            skill_registry=self.skill_registry,
        )
        player.gold += total_gold

        # Roll loot drops from defeated enemies
        loot_drops = combat_system.roll_loot(enemies)
        if loot_drops and feature("world_db"):
            from systems import inventory_system
            for item_id in loot_drops:
                inventory_system.pick_up_item(player, item_id, self.item_registry)
                bus.publish(Event("ITEM_FOUND", {"item_id": item_id,
                    "item_name": self.item_registry.get(item_id).name if self.item_registry.get(item_id) else item_id,
                    "rarity": getattr(self.item_registry.get(item_id), "rarity", "COMMON")}))

        renderer.print_divider()
        renderer.print_system_message("VICTORY", style="system_msg")
        from config import format_currency
        victory_lines = [f"XP gained: {total_xp}", f"Gold gained: {format_currency(total_gold)}"]
        if loot_drops:
            drop_names = []
            for item_id in loot_drops:
                item = self.item_registry.get(item_id)
                drop_names.append(item.name if item else item_id)
            victory_lines.append(f"Loot: {', '.join(drop_names)}")
        renderer.print_scene_text(victory_lines)
        if leveled_up:
            for lvl in leveled_up:
                renderer.print_system_message(f"LEVEL UP → {lvl}", style="system_msg")
            self._stat_allocation_prompt()

        bus.flush()
        renderer.prompt_any_key()
        _media.on_combat_end("victory")
        _slog.event("combat_end", encounter=encounter_id, result="victory", hp=self.state.player.current_hp)
        return "victory"

    def _stat_allocation_prompt(self) -> None:
        player = self.state.player
        while player.stat_points > 0:
            renderer.print_status_bar(player, self.item_registry)
            renderer.console.print(f"\n  [system_msg]You have {player.stat_points} stat point(s) to spend.[/system_msg]")
            stats = ["STR", "INT", "AGI", "LCK", "VIT", "WIS", "END", "Skip"]
            choice = uip.select("Allocate stat point:", choices=stats).ask()
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
