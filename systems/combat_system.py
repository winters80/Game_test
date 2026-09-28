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
    data = dict(template)
    data["current_hp"] = data["max_hp"]
    return Enemy(**data)


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
) -> tuple[int, bool]:
    """Basic attack. Returns (damage_dealt, is_critical).

    Folds in PASSIVE skill bonuses (attack/crit/bonus-damage-on-hit) and any
    TRIGGERED skills bound to ``on_attack``. Pass ``skill_registry`` to get
    full passive contributions — without it, the function falls back to the
    legacy stat-only formula.
    """
    from systems.passive_system import get_passive_modifiers, try_fire_trigger

    mods = get_passive_modifiers(player, skill_registry) if skill_registry else None

    base_dmg = max(1, player.stats.STR + random.randint(1, 4) - enemy.defense)
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

    # TRIGGERED skills firing on attack (Arcane Strike)
    if skill_registry is not None:
        trig_bonus = try_fire_trigger(player, "on_attack", skill_registry)
        damage += trig_bonus

    enemy.current_hp = max(0, enemy.current_hp - damage)

    # on_kill trigger (heal / buff after dropping an enemy)
    if skill_registry is not None and enemy.current_hp <= 0:
        kill_bonus = try_fire_trigger(player, "on_kill", skill_registry)
        if kill_bonus:
            player.current_hp = min(player.max_hp, player.current_hp + kill_bonus)

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
) -> int:
    """Enemy attacks player. Returns damage dealt.

    PASSIVE skills apply: defense_bonus subtracts flat damage,
    dodge_chance adds to the player's chance to dodge entirely.
    """
    from systems.passive_system import get_passive_modifiers

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
    if random.random() < dodge_chance:
        return 0  # dodged
    player.current_hp = max(0, player.current_hp - damage)
    return damage


def try_flee(player: "Player", enemy: Enemy) -> bool:
    """50% base flee chance, modified by AGI vs enemy rank."""
    flee_chance = 0.5 + (player.stats.AGI - 5) * 0.03
    return random.random() < flee_chance


def resolve_combat_auto(player: "Player", enemies: list[Enemy], skill_registry: "SkillRegistry") -> CombatResult:
    """Run a full combat to completion (used for encounters triggered by scene triggers)."""
    total_xp = 0
    total_gold = 0
    all_loot: list[str] = []
    turn = 0

    # Wrap enemies as dicts for status effect tracking
    enemy_status: dict[str, dict] = {
        e.enemy_id + str(i): {"hp": e.current_hp, "statuses": {}}
        for i, e in enumerate(enemies)
    }
    enemy_keys = list(enemy_status.keys())

    while any(e.is_alive for e in enemies) and player.current_hp > 0:
        turn += 1
        # Player attacks first alive enemy
        target = next((e for e in enemies if e.is_alive), None)
        if target:
            dmg, _ = player_attack(player, target)
            # Chance to apply bleed on physical attacks (10% base)
            if random.random() < 0.10:
                idx = enemies.index(target)
                ekey = enemy_keys[idx]
                enemy_status[ekey]["hp"] = target.current_hp
                apply_status_effect(enemy_status[ekey], "bleed")
            if not target.is_alive:
                total_xp += target.xp_reward
                total_gold += target.gold_reward
                all_loot.extend(roll_loot([target]))

        # Tick status effects and apply DoT to enemies, then let alive enemies attack
        for i, enemy in enumerate(enemies):
            if not enemy.is_alive:
                continue
            ekey = enemy_keys[i]
            enemy_status[ekey]["hp"] = enemy.current_hp
            status_msgs = tick_status_effects(enemy_status[ekey])
            # Sync hp back to Enemy object
            enemy.current_hp = enemy_status[ekey]["hp"]
            if enemy.current_hp <= 0:
                total_xp += enemy.xp_reward
                total_gold += enemy.gold_reward
                all_loot.extend(enemy.loot_table)
                continue  # enemy died from DoT
            if player.current_hp > 0:
                enemy_attack(enemy, player)

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
