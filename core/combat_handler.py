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

_BASIC = "⚔ Basic Attack"
_FLEE = "🏃 Flee"


def _number_duplicates(enemies: list) -> None:
    """Number duplicate names: "Goblin Scout #1", "Goblin Scout #2"."""
    counts: dict[str, int] = {}
    for e in enemies:
        counts[e.name] = counts.get(e.name, 0) + 1
    seen: dict[str, int] = {}
    for e in enemies:
        if counts[e.name] > 1:
            seen[e.name] = seen.get(e.name, 0) + 1
            e.name = f"{e.name} #{seen[e.name]}"


def _print_enemy_hp(enemy) -> None:
    renderer.console.print(f"  [dim_text]→ {enemy.name}: {enemy.current_hp}/{enemy.max_hp} HP remaining[/dim_text]")


def _combat_sounds(res, player, statuses: dict, guard: tuple) -> None:
    """The enemy's turn as sound: a ward or evade took it (block), a clean miss
    (dodge), a heavy hit (player_hurt) or a normal one (enemy_hit); and the
    heartbeat while HP is low. Then the enemy's own cues (enrage, summon, a
    heavy blow coming)."""
    from ui import media
    blocked = (statuses.get("_ward", 0), statuses.get("_evade", 0)) != guard
    missed = any(style == "miss" for _t, style in res.lines)
    if blocked:
        media.sfx("block")
    if res.damage:
        heavy = res.damage >= player.max_hp * 0.15
        if not (heavy and media.sfx("player_hurt")):
            media.sfx("enemy_hit")
    elif missed and not blocked:
        media.sfx("dodge")
    for cue in res.cues:
        media.sfx(cue)
    media.heartbeat(player.current_hp, player.max_hp)


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
        from systems import combat_effects as fx
        from systems.skill_system import reset_cooldowns_for_combat, tick_combat_cooldowns
        _media.on_combat_start(boss=any(getattr(e, "abilities", None) for e in (enemies or [])))
        _slog.event("combat_start", encounter=encounter_id, surprise=surprise,
                    enemies=[getattr(e, "name", e) for e in (enemies or [])] or None)
        if enemies is None:
            enemies = combat_system.spawn_encounter(encounter_id)
        self._last_killing_blow = "physical"
        if not enemies:
            return "none"
        _number_duplicates(enemies)
        self._scale_for_companions(encounter_id, enemies)
        from systems import difficulty
        if difficulty.is_hard(self.state.player) and difficulty.applies_to(encounter_id):
            difficulty.harden(enemies)

        renderer.print_divider()
        count = len(enemies)
        renderer.print_system_message(
            f"COMBAT INITIATED — {count} enem{'y' if count == 1 else 'ies'}: {', '.join(e.name for e in enemies)}",
            style="system_warning",
        )
        _time.sleep(0.5)

        player = self.state.player
        # Skill cooldowns are PER-ENCOUNTER: reset at the start of each fight and
        # tick once per combat round (end of the loop), never per menu step.
        reset_cooldowns_for_combat(player)
        player_statuses: dict = {}      # statuses end with the fight
        self._last_combat_rounds = 0
        turn = 0
        if surprise:
            self._surprise_strike(enemies, player)

        alive_enemies = [e for e in enemies if e.is_alive]
        while alive_enemies and player.current_hp > 0:
            turn += 1
            self._print_round(turn, alive_enemies, player, player_statuses)
            tactics = self._plan_tactics(alive_enemies, player, player_statuses)   # bosses think meanwhile
            if fx.consume_stun(player_statuses):
                renderer.print_combat_action("You", "are stunned and lose your turn!", style="system_warning")
            else:
                outcome = self._player_turn(alive_enemies, player, player_statuses)
                if outcome == "cancelled":
                    continue
                if outcome == "fled":
                    renderer.prompt_any_key()
                    _media.on_combat_end("fled")
                    _slog.event("combat_end", encounter=encounter_id, result="fled", hp=player.current_hp)
                    return "fled"
            party = self._companion_strikes(enemies)
            self._enemy_phase(enemies, player, player_statuses, tactics, party)
            self._end_of_round(enemies, player, player_statuses)
            self._last_combat_rounds = turn
            tick_combat_cooldowns(player)
            alive_enemies = [e for e in enemies if e.is_alive]
            _time.sleep(0.3)

        if player.current_hp <= 0:
            return self._combat_defeat(encounter_id, nonlethal)
        return self._combat_victory(encounter_id, enemies)

    # ── _run_combat, phase by phase ───────────────────────────────────────────

    def _surprise_strike(self, enemies: list, player) -> None:
        """A free attack on the first enemy before anyone can respond."""
        target = next((e for e in enemies if e.is_alive), None)
        if target is None:
            return
        fired: list = []
        dmg, is_crit = combat_system.player_attack(player, target, self.skill_registry, fired)
        renderer.print_combat_action(
            "SURPRISE!", f"You strike {target.name}{' CRITICALLY' if is_crit else ''} before they react!",
            dmg, "system_warning")
        _print_triggers(fired)
        if not target.is_alive:
            renderer.print_combat_action(target.name, "goes down before the fight begins!", style="success")
        _time.sleep(0.4)

    def _print_round(self, turn: int, alive_enemies: list, player, player_statuses: dict) -> None:
        """The round header: status bar, then every enemy with its HP bar."""
        from systems import combat_effects as fx
        renderer.clear()
        renderer.print_title()
        renderer.print_status_bar(player, self.item_registry)
        renderer.print_divider()
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

    def _player_turn(self, alive_enemies: list, player, player_statuses: dict) -> str:
        """Ask for and carry out the player's action. Returns "acted",
        "cancelled" (ask again) or "fled"."""
        from ui import media as _media
        # Only ACTIVE skills: PASSIVE ones work through systems/passive_system and
        # TRIGGERED ones fire on their own events (listing them would double-fire).
        skills_available = [
            sid for sid in player.skills
            if (sk := self.skill_registry.get(sid)) and sk.skill_type == SkillType.ACTIVE
        ]
        labels = {_skill_label(sid, player, self.skill_registry): sid for sid in skills_available}
        action = uip.select("Your action:", choices=[_BASIC, *labels, _FLEE]).ask()
        if action is None:
            return "cancelled"
        if action == _FLEE:
            if combat_system.try_flee(player, alive_enemies[0]):
                _media.sfx("dodge")
                renderer.print_scene_text(["You flee from combat!"])
                return "fled"
            renderer.print_scene_text(["You failed to flee!"])
            return "acted"
        target = self._pick_target(alive_enemies)
        if action == _BASIC:
            self._basic_attack(player, target)
        elif action in labels:
            self._use_skill(labels[action], player, target, player_statuses)
        return "acted"

    @staticmethod
    def _pick_target(alive_enemies: list):
        if len(alive_enemies) == 1:
            return alive_enemies[0]
        choices = [f"[{i}] {e.name}  ({e.current_hp}/{e.max_hp} HP)" for i, e in enumerate(alive_enemies, 1)]
        answer = uip.select("Target:", choices=choices).ask()
        try:
            return alive_enemies[int(answer.split("]")[0].lstrip("[")) - 1]
        except Exception:
            return alive_enemies[0]

    def _basic_attack(self, player, target) -> None:
        from ui import media as _media
        from systems import combat_effects as fx
        _media.sfx("swing")
        fired: list = []
        dmg, is_crit = combat_system.player_attack(player, target, self.skill_registry, fired)
        renderer.print_combat_action("You", f"strike {target.name}{' CRITICALLY' if is_crit else ''} for", dmg,
                                     "critical" if is_crit else "damage")
        _print_triggers(fired)
        for eff, chance in fx.trigger_statuses(fired):
            if target.is_alive and random.random() < chance and fx.inflict(target.statuses, eff, target.rank):
                renderer.print_combat_action(target.name, f"is {eff}ed" if eff == "poison" else f"suffers {eff}",
                                             style="success")
        _print_enemy_hp(target)
        if not target.is_alive:
            self._last_killing_blow = "physical"
            renderer.print_combat_action(target.name, "is defeated!", style="success")

    def _use_skill(self, skill_id: str, player, target, player_statuses: dict) -> None:
        """Cast an ACTIVE skill: a ward / evade, a heal, or an attack (with its status)."""
        from entities.enums import EffectType
        from systems import combat_effects as fx
        from systems.skill_system import calculate_skill_damage, record_skill_use
        skill = self.skill_registry.get(skill_id)
        cd = player.skill_cooldowns.get(skill_id, 0)
        if cd > 0:
            renderer.print_scene_text([f"{skill.name} is on cooldown ({cd} turns remaining)."])
            return
        if skill.mp_cost > 0 and player.current_mp < skill.mp_cost:
            renderer.print_scene_text([f"Not enough MP! Need {skill.mp_cost}, have {player.current_mp}."])
            return
        defensive = fx.is_defensive(skill)
        if defensive or any(e.effect_type == EffectType.HEAL for e in skill.effects or []):
            player.current_mp -= skill.mp_cost
            if skill.cooldown_turns > 0:
                player.skill_cooldowns[skill_id] = skill.cooldown_turns
            if defensive:
                renderer.print_scene_text([fx.apply_defensive(skill, player, player_statuses)])
            else:
                heal_val = calculate_skill_damage(skill, player)
                player.current_hp = min(player.max_hp, player.current_hp + heal_val)
                renderer.print_scene_text([f"You cast {skill.name}. Healed {heal_val} HP. "
                                           f"({player.current_hp}/{player.max_hp})"])
            record_skill_use(player, skill_id, self.skill_registry)        # heals level up too
            return
        val, success = combat_system.player_skill_attack(player, skill_id, target, self.skill_registry)
        if not success:
            renderer.print_scene_text(["Not enough MP!"])
        else:
            if skill.cooldown_turns > 0:
                player.skill_cooldowns[skill_id] = skill.cooldown_turns
            renderer.print_combat_action("You", f"cast {skill.name} on {target.name} for", val, "rare")
            status = fx.skill_status(skill)
            if status and target.is_alive and random.random() < status[1]:
                if fx.inflict(target.statuses, status[0], target.rank):
                    renderer.print_combat_action(target.name, f"is afflicted: {status[0]}", style="success")
                else:
                    renderer.print_combat_action(target.name, f"resists {status[0]}", style="miss")
            _print_enemy_hp(target)
        if not target.is_alive:
            self._last_killing_blow = lair_system.killing_blow_kind(skill)
            renderer.print_combat_action(target.name, "is defeated!", style="success")

    def _companion_strikes(self, enemies: list) -> list:
        """Each standing companion (a bot on this Gate run) hits a random enemy.
        Returns the companions still standing."""
        from systems.bot_brain import bot_attack
        party = [c for c in (getattr(self, "_companions", None) or []) if c.hp > 1]
        for comp in party:
            alive = [e for e in enemies if e.is_alive]
            if not alive:
                break
            foe = random.choice(alive)
            hit = max(1, round((bot_attack(comp) - foe.defense) * random.uniform(0.8, 1.2)))
            foe.current_hp = max(0, foe.current_hp - hit)
            renderer.print_combat_action(comp.name, f"strikes {foe.name} for", hit, "rare")
            if not foe.is_alive:
                renderer.print_combat_action(foe.name, "is defeated!", style="success")
        return party

    def _enemy_phase(self, enemies: list, player, player_statuses: dict, tactics, party: list) -> None:
        """Every living enemy acts: on a companion (who draws up to 60% of
        attacks) or on the player, with its sounds and lines."""
        from systems import combat_effects as fx
        from systems.bot_brain import bot_defense
        for enemy in [e for e in enemies if e.is_alive]:
            party = [c for c in party if c.hp > 1]
            if (party and enemy.is_alive and not enemy.statuses.get("stun")
                    and random.random() < min(0.6, COMPANION_DRAW_CHANCE * len(party))):
                comp = random.choice(party)
                taken = max(1, enemy.attack - bot_defense(comp))
                comp.hp = max(1, comp.hp - taken)
                renderer.print_combat_action(enemy.name, f"turns on {comp.name} for", taken, "damage")
                if comp.hp <= 1:
                    renderer.print_combat_action(comp.name, "is knocked down and drags themself out of the fight.",
                                                 style="system_warning")
                continue
            if not (enemy.is_alive and player.current_hp > 0):
                continue
            guard = (player_statuses.get("_ward", 0), player_statuses.get("_evade", 0))
            res = fx.enemy_turn(enemy, player, player_statuses, self.skill_registry, enemies,
                                chosen=self._tactic_for(tactics, enemy))
            _combat_sounds(res, player, player_statuses, guard)
            for text, style in res.lines:
                renderer.console.print(f"  [{style}]{text}[/{style}]")
            _print_triggers(res.fired)
            if res.damage:
                renderer.console.print(f"  [dim_text]→ Your HP: {player.current_hp}/{player.max_hp}[/dim_text]")

    @staticmethod
    def _end_of_round(enemies: list, player, player_statuses: dict) -> None:
        """Damage over time on both sides, then the player's MP regen."""
        from systems import combat_effects as fx
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

    def _combat_defeat(self, encounter_id: str, nonlethal: bool) -> str:
        """The player fell: beaten down (wardens), a life spent, or game over."""
        from utils import session_log as _slog
        from ui import media as _media
        player = self.state.player
        if nonlethal:
            # Wardens and arrests beat you down; they don't kill you.
            player.current_hp = 1
            renderer.print_system_message("YOU ARE BEATEN DOWN.", style="system_warning")
        else:
            renderer.print_system_message("YOU HAVE FALLEN.", style="system_warning")
            logger.warning(
                "Player died in combat: encounter=%s zone=%s level=%d hp=%d/%d lives=%d",
                encounter_id, self.state.current_scene_id,
                player.level, player.current_hp, player.max_hp, player.lives_remaining,
            )
            if feature("lives_system"):
                from systems import lives_system
                survived = lives_system.handle_death(self.state, cause=f"combat:{encounter_id}",
                                                     zone_id=self.state.current_scene_id)
                bus.flush()
                _media.sfx("life_lost")
                if not survived:
                    logger.warning("Player game-over: all lives spent. encounter=%s", encounter_id)
                    renderer.print_system_message("ALL LIVES SPENT. GAME OVER.", style="system_warning")
                    _media.system_voice("game_over")
                    renderer.print_scene_text(["The System erases your record. You are gone."])
                    self._running = False
                else:
                    _media.system_voice("life_lost")
                    lives = player.lives_remaining
                    renderer.print_scene_text([
                        f"A life burns away. {lives} life{'s' if lives != 1 else ''} remaining.",
                        "You wake at your last safe point, battered but breathing.",
                    ])
            else:
                renderer.print_scene_text(["The System records your defeat."])
                self._running = False
            renderer.prompt_any_key()
        _media.on_combat_end("defeat")
        _slog.event("combat_end", encounter=encounter_id, result="defeat", hp=player.current_hp)
        return "defeat"

    def _combat_victory(self, encounter_id: str, enemies: list) -> str:
        """XP, gold and loot, the victory lines, and any level-ups."""
        from config import format_currency
        from utils import session_log as _slog
        from ui import media as _media
        player = self.state.player
        full_xp = round(sum(e.xp_reward for e in enemies) * self._gate_xp_scale(encounter_id))
        rated = self._content_level(encounter_id)
        total_xp = level_system.scaled_xp(full_xp, player.level, rated)
        total_gold = sum(e.gold_reward for e in enemies)
        from systems import difficulty
        hard = difficulty.is_hard(player) and difficulty.applies_to(encounter_id)
        if hard:
            total_gold = difficulty.hard_gold(total_gold)
        # The registries let add_experience auto-grant the class's next skill on level-up.
        leveled_up = level_system.add_experience(player, total_xp, class_registry=self.class_registry,
                                                 skill_registry=self.skill_registry)
        player.gold += total_gold

        loot_drops = combat_system.roll_loot(enemies)
        names = []
        for item_id in loot_drops:
            item = self.item_registry.get(item_id)
            names.append(item.name if item else item_id)
            if feature("world_db"):
                from systems import inventory_system
                inventory_system.pick_up_item(player, item_id, self.item_registry)
                bus.publish(Event("ITEM_FOUND", {"item_id": item_id, "item_name": names[-1],
                                                 "rarity": getattr(item, "rarity", "COMMON")}))

        renderer.print_divider()
        renderer.print_system_message("VICTORY", style="system_msg")
        xp_line = f"XP gained: {total_xp}"
        if total_xp < full_xp:
            xp_line += f" (of {full_xp}: this is level {rated} work, and you're level {player.level})"
        victory_lines = [xp_line, f"Gold gained: {format_currency(total_gold)}" + (" (Hard mode +25%)" if hard else "")]
        if names:
            victory_lines.append(f"Loot: {', '.join(names)}")
        renderer.print_scene_text(victory_lines)
        for lvl in leveled_up or []:
            renderer.print_system_message(f"LEVEL UP → {lvl}", style="system_msg")
        if leveled_up:
            self._stat_allocation_prompt()

        bus.flush()
        renderer.prompt_any_key()
        _media.on_combat_end("victory")
        _slog.event("combat_end", encounter=encounter_id, result="victory", hp=player.current_hp)
        return "victory"

    def _scale_for_companions(self, encounter_id: str, enemies: list) -> None:
        """In a solo Gate, each companion the player brought gives every enemy
        GATE_COMPANION_HP more HP. A companion draws up to 60% of attacks: without
        this a duo run left the player ~75% HP where alone it left ~50%. Group
        Gates already field more enemies for their party."""
        from config import GATE_COMPANION_HP
        party = getattr(self, "_companions", None) or []
        kind, _, rest = (encounter_id or "").partition(":")
        gate = (self.gate_registry.get(rest.split(":")[0])
                if party and kind in ("gate", "lair") and getattr(self, "gate_registry", None) else None)
        if gate is None or gate.party_size > 1:
            return
        factor = 1 + GATE_COMPANION_HP * len(party)
        for e in enemies:
            e.max_hp = max(1, round(e.max_hp * factor))
            e.current_hp = max(1, round(e.current_hp * factor))

    def _gate_xp_scale(self, encounter_id: str) -> float:
        """gate_flow.gate_xp_scale for a Gate's rooms and master, else 1."""
        kind, _, rest = (encounter_id or "").partition(":")
        gate = (self.gate_registry.get(rest.split(":")[0])
                if kind in ("gate", "lair") and getattr(self, "gate_registry", None) else None)
        if gate is None:
            return 1.0
        from core.gate_flow import gate_xp_scale
        return gate_xp_scale(gate)

    def _content_level(self, encounter_id: str) -> int:
        """Rated level of a fight, for the XP falloff: a Gate's level for its rooms
        and master, a dungeon lair's level, a dungeon encounter's ENCOUNTER_LEVELS (0 = unrated)."""
        kind, _, rest = (encounter_id or "").partition(":")
        if kind in ("gate", "lair"):
            ref = rest.split(":")[0]
            gate = self.gate_registry.get(ref) if getattr(self, "gate_registry", None) else None
            if gate is not None:
                return gate.min_level
            lair = self.lair_registry.get(ref) if getattr(self, "lair_registry", None) else None
            return int(getattr(lair, "min_level", 0) or 0) if lair is not None else 0
        return combat_system.ENCOUNTER_LEVELS.get(encounter_id, 0)

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
