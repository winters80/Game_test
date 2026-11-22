from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from systems.level_system import add_experience
from systems.skill_system import calculate_skill_damage
from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.skill import SkillRegistry
    from systems.passive_system import TriggerFiring


# ── Status Effects ────────────────────────────────────────────────────────────
STATUS_EFFECTS = {
    "poison":  {"damage_per_turn": 3,  "duration": 3, "color": "green",  "message": "poisoned"},
    "bleed":   {"damage_per_turn": 2,  "duration": 4, "color": "red",    "message": "bleeding"},
    "burn":    {"damage_per_turn": 4,  "duration": 2, "color": "damage", "message": "burning"},
    "stun":    {"damage_per_turn": 0,  "duration": 1, "color": "gold",   "message": "stunned — loses next action"},
    "frozen":  {"damage_per_turn": 0,  "duration": 2, "color": "cyan",   "message": "frozen — -3 AGI"},
}


def apply_status_effect(target_data: dict, effect_name: str) -> str:
    """Apply a status effect to a combatant dict. Returns description."""
    if "statuses" not in target_data:
        target_data["statuses"] = {}
    info = STATUS_EFFECTS.get(effect_name, {})
    duration = info.get("duration", 2)
    target_data["statuses"][effect_name] = duration
    return f"{effect_name} ({duration} turns)"


def tick_status_effects(target_data: dict) -> list[str]:
    """
    Tick all active status effects on a combatant.
    Returns list of damage/message strings. Modifies target_data["hp"] directly.
    """
    messages = []
    if "statuses" not in target_data:
        return messages
    expired = []
    for effect, turns_left in list(target_data["statuses"].items()):
        info = STATUS_EFFECTS.get(effect, {})
        dot = info.get("damage_per_turn", 0)
        if dot > 0:
            target_data["hp"] = max(0, target_data["hp"] - dot)
            messages.append(f"  [{info.get('color','scene_text')}]{effect.capitalize()}: {dot} damage[/{info.get('color','scene_text')}]  ({target_data['hp']} HP remaining)")
        turns_left -= 1
        if turns_left <= 0:
            expired.append(effect)
            messages.append(f"  [dim_text]{effect.capitalize()} wears off.[/dim_text]")
        else:
            target_data["statuses"][effect] = turns_left
    for e in expired:
        del target_data["statuses"][e]
    return messages


@dataclass
class Enemy:
    enemy_id: str
    name: str
    max_hp: int
    current_hp: int
    attack: int
    defense: int = 0
    xp_reward: int = 10
    gold_reward: int = 5
    loot_table: list[str] = field(default_factory=list)
    rank: str = "F"
    # (status, chance) inflicted on hit, e.g. ("poison", 0.3); applied by
    # systems/combat_effects.enemy_turn.
    status_on_hit: tuple[str, float] | None = None
    statuses: dict = field(default_factory=dict)      # {status: turns left}
    # Signature moves: [{name, kind: heavy|status|heal|summon|drain, every, power, status, summon}]
    abilities: list = field(default_factory=list)
    enrage_below: float = 0.0       # share of max HP; hits 25% harder once below it
    enraged: bool = False
    turns_taken: int = 0
    summoned: bool = False
    ability_last: dict = field(default_factory=dict)  # ability name → turn last used (tactics)

    @property
    def is_alive(self) -> bool:
        return self.current_hp > 0


# Pre-defined enemy templates
ENEMY_TEMPLATES: dict[str, dict] = {
    "goblin_scout": {
        "enemy_id": "goblin_scout",
        "name": "Goblin Scout",
        "max_hp": 15, "current_hp": 15,
        "attack": 4, "defense": 1,
        "xp_reward": 10, "gold_reward": 300,
        "rank": "F",
        "loot_table": [("forest_herb", 0.4), ("empty_vial", 0.2)],
    },
    "goblin_looter": {
        "enemy_id": "goblin_looter",
        "name": "Goblin Looter",
        "max_hp": 20, "current_hp": 20,
        "attack": 5, "defense": 2,
        "xp_reward": 15, "gold_reward": 800,
        "rank": "F",
        "loot_table": [("forest_herb", 0.5), ("empty_vial", 0.35), ("herb_bundle", 0.2)],
    },
    "dungeon_slime": {
        "enemy_id": "dungeon_slime",
        "name": "Dungeon Slime",
        "max_hp": 25, "current_hp": 25,
        "attack": 3, "defense": 0,
        "xp_reward": 12, "gold_reward": 200,
        "rank": "F",
        "loot_table": [("herb_bundle", 0.3), ("empty_vial", 0.4)],
    },
    "dungeon_wolf": {
        "enemy_id": "dungeon_wolf",
        "name": "Dungeon Wolf",
        "max_hp": 28, "current_hp": 28,
        "attack": 7, "defense": 2,
        "xp_reward": 18, "gold_reward": 0,
        "rank": "F",
        "loot_table": [("cooked_meat", 0.6), ("cooked_meat", 0.3)],
    },
    "crystal_spider": {
        "enemy_id": "crystal_spider",
        "name": "Crystal Spider",
        "max_hp": 18, "current_hp": 18,
        "attack": 6, "defense": 1,
        "xp_reward": 20, "gold_reward": 100,
        "rank": "F",
        "status_on_hit": ("poison", 0.30),
        "loot_table": [("void_crystal", 0.15), ("empty_vial", 0.4)],
    },
    "corrupted_golem": {
        "enemy_id": "corrupted_golem",
        "name": "Corrupted Golem",
        "max_hp": 55, "current_hp": 55,
        "attack": 12, "defense": 6,
        "xp_reward": 60, "gold_reward": 500,
        "rank": "E",
        "loot_table": [("mountain_root", 0.4), ("void_crystal", 0.2)],
    },
    "fracture_wraith": {
        "enemy_id": "fracture_wraith",
        "name": "Fracture Wraith",
        "max_hp": 40, "current_hp": 40,
        "attack": 10, "defense": 3,
        "xp_reward": 45, "gold_reward": 0,
        "rank": "E",
        "status_on_hit": ("bleed", 0.20),
        "loot_table": [("void_crystal", 0.25)],
    },
    "cave_troll": {
        "enemy_id": "cave_troll",
        "name": "Cave Troll",
        "max_hp": 50, "current_hp": 50,
        "attack": 11, "defense": 4,
        "xp_reward": 50, "gold_reward": 600,
        "rank": "E",
        "loot_table": [("mountain_root", 0.4), ("cooked_meat", 0.3)],
    },
    "crystal_golem": {
        "enemy_id": "crystal_golem",
        "name": "Crystal Golem",
        "max_hp": 90, "current_hp": 90,
        "attack": 14, "defense": 7,
        "xp_reward": 140, "gold_reward": 1500,
        "rank": "D",
        "loot_table": [("void_crystal", 0.6), ("mountain_root", 0.5)],
        "abilities": [{"name": "Crystal Fist", "kind": "heavy", "every": 3, "power": 1.8},
                      {"name": "Shard Spray", "kind": "status", "every": 4, "status": "bleed"}],
        "enrage_below": 0.35,
    },
    "unknown_creature": {
        "enemy_id": "unknown_creature",
        "name": "??? [UNREGISTERED]",
        "max_hp": 80, "current_hp": 80,
        "attack": 18, "defense": 8,
        "xp_reward": 150, "gold_reward": 0,
        "rank": "C",
        "loot_table": [("void_crystal", 0.5), ("mountain_root", 0.3)],
    },
    "street_thugs": {
        "enemy_id": "street_thugs",
        "name": "Street Thugs",
        "max_hp": 22, "current_hp": 22,
        "attack": 6, "defense": 2,
        "xp_reward": 20, "gold_reward": 400,
        "rank": "F",
        "loot_table": [],
    },
}


def spawn_enemy(enemy_id: str) -> Enemy | None:
    template = ENEMY_TEMPLATES.get(enemy_id)
    if not template:
        return None
    import copy
    data = copy.deepcopy(template)
    data["current_hp"] = data["max_hp"]
    if isinstance(data.get("status_on_hit"), list):
        data["status_on_hit"] = tuple(data["status_on_hit"])
    known = Enemy.__dataclass_fields__
    return Enemy(**{k: v for k, v in data.items() if k in known})


# encounter_id → enemy template ids. Scene `combat:X` triggers name these.
ENCOUNTERS: dict[str, list[str]] = {
    "goblin_patrol":      ["goblin_scout", "goblin_scout", "goblin_looter"],
    "single_slime":       ["dungeon_slime"],
    "wolf_pack":          ["dungeon_wolf", "dungeon_wolf"],
    "crystal_spider_den": ["crystal_spider", "crystal_spider", "crystal_spider"],
    "corrupted_golem":    ["corrupted_golem"],
    "fracture_wraith":    ["fracture_wraith", "fracture_wraith"],
    "unknown_creature":   ["unknown_creature"],
    "street_thugs":       ["street_thugs"],
    "troll_patrol":       ["cave_troll", "goblin_scout"],
    "crystal_golem":      ["crystal_golem"],          # Floor 2 boss
}


def spawn_encounter(encounter_id: str) -> list[Enemy]:
    ids = ENCOUNTERS.get(encounter_id, [])
    enemies = [spawn_enemy(eid) for eid in ids]
    return [e for e in enemies if e is not None]


def roll_loot(enemies: list[Enemy]) -> list[str]:
    """Roll loot drops from all defeated enemies. Returns list of item_ids."""
    import random
    loot: list[str] = []
    for e in enemies:
        tmpl = ENEMY_TEMPLATES.get(e.enemy_id, {})
        for item_id, chance in tmpl.get("loot_table", []):
            if random.random() < chance:
                loot.append(item_id)
    return loot


@dataclass
class CombatResult:
    victory: bool
    xp_gained: int = 0
    gold_gained: int = 0
    loot: list[str] = field(default_factory=list)
    fled: bool = False


def player_attack(
    player: "Player", enemy: Enemy,
    skill_registry: "SkillRegistry | None" = None,
    fired: "list[TriggerFiring] | None" = None,
) -> tuple[int, bool]:
    """Basic attack. Returns (damage_dealt, is_critical).

    Folds in PASSIVE skill bonuses (attack/crit/bonus-damage-on-hit) and any
    TRIGGERED skills bound to ``on_attack`` / ``on_hit`` / ``on_kill``. Pass
    ``skill_registry`` to get full passive contributions — without it, the
    function falls back to the legacy stat-only formula. Pass a ``fired``
    list to collect the TRIGGERED skills that fired, for the combat log.
    """
    from systems.passive_system import fire_triggers, get_passive_modifiers

    fired = fired if fired is not None else []

    mods = get_passive_modifiers(player, skill_registry) if skill_registry else None

    # Finesse: a basic attack uses the better of STR and AGI (rogues, rangers).
    base_dmg = max(1, max(player.stats.STR, player.stats.AGI) + random.randint(1, 4) - enemy.defense)
    if mods:
        base_dmg += mods.attack_bonus
    crit_chance = player.stats.LCK * 0.01  # 1% per LCK
    if mods:
        crit_chance += mods.crit_chance
    is_crit = random.random() < crit_chance
    damage = int(base_dmg * 1.5) if is_crit else base_dmg

    # PASSIVE bonus damage on every basic attack (Void Infusion etc.)
    if mods:
        damage += mods.bonus_damage_on_hit

    from systems.synergy_system import apply_synergy_bonuses
    damage, _ = apply_synergy_bonuses(player, damage, 0)

    # TRIGGERED skills firing on attack / on hit (Arcane Strike is on_hit).
    # Basic attacks can't miss, so every attack is also a hit.
    if skill_registry is not None:
        for event in ("on_attack", "on_hit"):
            hits = fire_triggers(player, event, skill_registry)
            damage += sum(f.value for f in hits)
            fired.extend(hits)

    enemy.current_hp = max(0, enemy.current_hp - damage)

    # on_kill trigger (heal / buff after dropping an enemy)
    if skill_registry is not None and enemy.current_hp <= 0:
        kills = fire_triggers(player, "on_kill", skill_registry)
        kill_bonus = sum(f.value for f in kills)
        if kill_bonus:
            player.current_hp = min(player.max_hp, player.current_hp + kill_bonus)
        fired.extend(kills)

    return damage, is_crit


def player_skill_attack(player: "Player", skill_id: str, enemy: Enemy, skill_registry: "SkillRegistry") -> tuple[int, bool]:
    """Use a skill. Returns (value, success).

    Also bumps the skill's use count (potential level-up via
    ``skill_system.record_skill_use``).
    """
    skill = skill_registry.get(skill_id)
    if not skill or skill.mp_cost > player.current_mp:
        return 0, False
    player.current_mp -= skill.mp_cost
    value = calculate_skill_damage(skill, player)
    enemy.current_hp = max(0, enemy.current_hp - value)
    # Skills grow stronger with use (LitRPG progression)
    from systems.skill_system import record_skill_use
    record_skill_use(player, skill_id, skill_registry)
    return value, True


def enemy_attack(
    enemy: Enemy, player: "Player",
    skill_registry: "SkillRegistry | None" = None,
    fired: "list[TriggerFiring] | None" = None,
    player_statuses: dict | None = None,
) -> int:
    """Enemy attacks player. Returns damage dealt. A frozen player
    (``player_statuses``) dodges less.

    PASSIVE skills apply: defense_bonus subtracts flat damage,
    dodge_chance adds to the player's chance to dodge entirely.

    TRIGGERED ``on_low_hp`` skills fire when this hit takes the player from
    at/above ``LOW_HP_TRIGGER_THRESHOLD`` of max HP to below it without
    killing them; their value heals the player. Firing on the crossing
    (rather than "while low") means one hit = at most one firing, and it
    can only fire again after the player climbs back above the threshold.
    The returned damage is the raw hit, before any trigger heal. Pass a
    ``fired`` list to collect the TRIGGERED skills that fired.
    """
    from config import LOW_HP_TRIGGER_THRESHOLD
    from systems.passive_system import fire_triggers, get_passive_modifiers

    mods = get_passive_modifiers(player, skill_registry) if skill_registry else None

    damage = max(1, enemy.attack + random.randint(-1, 2) - max(0, player.stats.END // 3))
    if mods:
        damage = max(1, damage - mods.defense_bonus)

    from systems.synergy_system import get_active_synergies
    _synergies = get_active_synergies(player)
    for _syn in _synergies:
        if "damage_reduction" in _syn["bonus"]:
            damage = max(1, int(damage * (1 - _syn["bonus"]["damage_reduction"] / 100)))
    _extra_dodge = sum(_syn["bonus"].get("dodge_pct", 0) / 100 for _syn in _synergies)
    dodge_chance = player.stats.AGI * 0.008 + _extra_dodge
    if mods:
        dodge_chance += mods.dodge_chance
    if player_statuses and player_statuses.get("frozen"):
        from systems.combat_effects import FROZEN_DODGE_PENALTY
        dodge_chance = max(0.0, dodge_chance - FROZEN_DODGE_PENALTY)
    if random.random() < dodge_chance:
        return 0  # dodged
    if player_statuses:
        from systems.combat_effects import absorb
        damage, _note = absorb(player_statuses, damage)
        if damage <= 0:
            return 0
    hp_before = player.current_hp
    player.current_hp = max(0, player.current_hp - damage)

    threshold = player.max_hp * LOW_HP_TRIGGER_THRESHOLD
    if (
        skill_registry is not None
        and 0 < player.current_hp < threshold <= hp_before
    ):
        lows = fire_triggers(player, "on_low_hp", skill_registry)
        heal = sum(f.value for f in lows)
        if heal:
            player.current_hp = min(player.max_hp, player.current_hp + heal)
        if fired is not None:
            fired.extend(lows)
    return damage


def try_flee(player: "Player", enemy: Enemy) -> bool:
    """50% base flee chance, modified by AGI vs enemy rank."""
    flee_chance = 0.5 + (player.stats.AGI - 5) * 0.03
    return random.random() < flee_chance


def combat_mp_regen(player: "Player") -> int:
    """MP a player recovers at the end of each combat round: 1 + WIS // 5.
    Keeps casters casting in long fights. Returns the amount restored."""
    before = player.current_mp
    player.current_mp = min(player.max_mp, player.current_mp + 1 + player.stats.WIS // 5)
    return player.current_mp - before


def _best_skill(player: "Player", skill_registry: "SkillRegistry | None",
                statuses: dict | None = None) -> str | None:
    """The ACTIVE skill a player would pick now: a defensive one (ward / evade)
    when below 60% HP with none up, else the hardest-hitting damage skill
    that's affordable and off cooldown."""
    from entities.enums import EffectType, SkillType
    from systems.combat_effects import is_defensive
    if skill_registry is None:
        return None
    if player.current_hp < player.max_hp * 0.6 and not (statuses or {}).get("_ward") \
            and not (statuses or {}).get("_evade"):
        for sid in player.skills:
            sk = skill_registry.get(sid)
            if (sk and sk.skill_type == SkillType.ACTIVE and is_defensive(sk)
                    and sk.mp_cost <= player.current_mp and player.skill_cooldowns.get(sid, 0) <= 0):
                return sid
    best, best_val = None, 0
    for sid in player.skills:
        sk = skill_registry.get(sid)
        if (not sk or sk.skill_type != SkillType.ACTIVE or sk.mp_cost > player.current_mp
                or player.skill_cooldowns.get(sid, 0) > 0
                or not any(e.effect_type == EffectType.DAMAGE for e in sk.effects)):
            continue
        val = calculate_skill_damage(sk, player)
        if val > best_val:
            best, best_val = sid, val
    return best


def resolve_combat_auto(player: "Player", enemies: list[Enemy], skill_registry: "SkillRegistry",
                        companions: list | None = None, use_skills: bool = False) -> CombatResult:
    """Run a full combat to completion without player input.

    Interactive fights go through ``core/combat_handler._run_combat``; this
    is the headless equivalent. It applies the same skill rules: PASSIVE
    and TRIGGERED skills via ``skill_registry``, per-combat cooldowns reset
    at the start and ticked each round.
    """
    from systems.skill_system import reset_cooldowns_for_combat, tick_combat_cooldowns

    reset_cooldowns_for_combat(player)
    total_xp = 0
    total_gold = 0
    all_loot: list[str] = []
    turn = 0

    from systems import combat_effects as fx

    player_statuses: dict = {}
    enemies = list(enemies)
    counted: set[int] = set()

    def _collect(e) -> None:
        nonlocal total_xp, total_gold
        if not e.is_alive and id(e) not in counted:
            counted.add(id(e))
            total_xp += e.xp_reward
            total_gold += e.gold_reward
            all_loot.extend(roll_loot([e]))

    while any(e.is_alive for e in enemies) and player.current_hp > 0:
        turn += 1
        target = next((e for e in enemies if e.is_alive), None)
        if target and not fx.consume_stun(player_statuses):
            sid = _best_skill(player, skill_registry, player_statuses) if use_skills else None
            sk = skill_registry.get(sid) if sid else None
            if sk is not None and fx.is_defensive(sk):
                player.current_mp -= sk.mp_cost
                fx.apply_defensive(sk, player, player_statuses)
                if sk.cooldown_turns:
                    player.skill_cooldowns[sid] = sk.cooldown_turns
            elif sid:
                value, ok = player_skill_attack(player, sid, target, skill_registry)
                if ok and sk.cooldown_turns:
                    player.skill_cooldowns[sid] = sk.cooldown_turns
                st = fx.skill_status(sk)
                if ok and st and target.is_alive and random.random() < st[1]:
                    fx.inflict(target.statuses, st[0], target.rank)
            else:
                fired: list = []
                player_attack(player, target, skill_registry, fired)
                for eff, chance in fx.trigger_statuses(fired):
                    if target.is_alive and random.random() < chance:
                        fx.inflict(target.statuses, eff, target.rank)
            _collect(target)

        # Bot companions (same rules as core/combat_handler): strike, and draw attacks.
        party = [c for c in (companions or []) if c.hp > 1]
        if party:
            from systems.bot_brain import bot_attack, bot_defense
            for comp in party:
                foe = next((e for e in enemies if e.is_alive), None)
                if foe:
                    foe.current_hp = max(0, foe.current_hp - max(1, bot_attack(comp) - foe.defense))
                    _collect(foe)

        for enemy in list(enemies):
            party = [c for c in party if c.hp > 1]
            if party and enemy.is_alive and random.random() < min(0.6, 0.3 * len(party)):
                comp = random.choice(party)
                comp.hp = max(1, comp.hp - max(1, enemy.attack - bot_defense(comp)))
                continue
            if enemy.is_alive and player.current_hp > 0:
                fx.enemy_turn(enemy, player, player_statuses, skill_registry, enemies)

        # End of round: damage over time on everyone.
        for enemy in enemies:
            if enemy.is_alive:
                fx.tick_enemy(enemy)
                _collect(enemy)
        if player.current_hp > 0:
            player.current_hp, _ = fx.tick(player_statuses, player.current_hp)
            combat_mp_regen(player)

        tick_combat_cooldowns(player)
        if turn > 50:  # safety cap
            break

    victory = player.current_hp > 0
    if victory:
        leveled_up = add_experience(player, total_xp)
        player.gold += total_gold
        for item_id in all_loot:
            player.add_item(item_id)

    return CombatResult(
        victory=victory,
        xp_gained=total_xp if victory else 0,
        gold_gained=total_gold if victory else 0,
        loot=all_loot if victory else [],
    )
