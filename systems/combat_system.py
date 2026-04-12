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
        "xp_reward": 10, "gold_reward": 3,
        "rank": "F",
    },
    "goblin_looter": {
        "enemy_id": "goblin_looter",
        "name": "Goblin Looter",
        "max_hp": 20, "current_hp": 20,
        "attack": 5, "defense": 2,
        "xp_reward": 15, "gold_reward": 8,
        "rank": "F",
    },
    "dungeon_slime": {
        "enemy_id": "dungeon_slime",
        "name": "Dungeon Slime",
        "max_hp": 25, "current_hp": 25,
        "attack": 3, "defense": 0,
        "xp_reward": 12, "gold_reward": 2,
        "rank": "F",
    },
    "unknown_creature": {
        "enemy_id": "unknown_creature",
        "name": "??? [UNREGISTERED]",
        "max_hp": 80, "current_hp": 80,
        "attack": 18, "defense": 8,
        "xp_reward": 150, "gold_reward": 0,
        "rank": "C",
    },
}


def spawn_enemy(enemy_id: str) -> Enemy | None:
    template = ENEMY_TEMPLATES.get(enemy_id)
    if not template:
        return None
    data = dict(template)
    data["current_hp"] = data["max_hp"]
    return Enemy(**data)


def spawn_encounter(encounter_id: str) -> list[Enemy]:
    encounters: dict[str, list[str]] = {
        "goblin_patrol": ["goblin_scout", "goblin_scout", "goblin_looter"],
        "single_slime": ["dungeon_slime"],
        "unknown_creature": ["unknown_creature"],
    }
    ids = encounters.get(encounter_id, [])
    enemies = [spawn_enemy(eid) for eid in ids]
    return [e for e in enemies if e is not None]


@dataclass
class CombatResult:
    victory: bool
    xp_gained: int = 0
    gold_gained: int = 0
    loot: list[str] = field(default_factory=list)
    fled: bool = False


def player_attack(player: "Player", enemy: Enemy) -> tuple[int, bool]:
    """Basic attack. Returns (damage_dealt, is_critical)."""
    base_dmg = max(1, player.stats.STR + random.randint(1, 4) - enemy.defense)
    crit_chance = player.stats.LCK * 0.01  # 1% per LCK point
    is_crit = random.random() < crit_chance
    damage = int(base_dmg * 1.5) if is_crit else base_dmg
    enemy.current_hp = max(0, enemy.current_hp - damage)
    return damage, is_crit


def player_skill_attack(player: "Player", skill_id: str, enemy: Enemy, skill_registry: "SkillRegistry") -> tuple[int, bool]:
    """Use a skill. Returns (value, success)."""
    skill = skill_registry.get(skill_id)
    if not skill or skill.mp_cost > player.current_mp:
        return 0, False
    player.current_mp -= skill.mp_cost
    value = calculate_skill_damage(skill, player)
    enemy.current_hp = max(0, enemy.current_hp - value)
    return value, True


def enemy_attack(enemy: Enemy, player: "Player") -> int:
    """Enemy attacks player. Returns damage dealt."""
    damage = max(1, enemy.attack + random.randint(-1, 2) - max(0, player.stats.END // 3))
    dodge_chance = player.stats.AGI * 0.008
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

    while any(e.is_alive for e in enemies) and player.current_hp > 0:
        turn += 1
        # Player attacks first alive enemy
        target = next((e for e in enemies if e.is_alive), None)
        if target:
            dmg, _ = player_attack(player, target)
            if not target.is_alive:
                total_xp += target.xp_reward
                total_gold += target.gold_reward
                all_loot.extend(target.loot_table)

        # All alive enemies attack player
        for enemy in enemies:
            if enemy.is_alive and player.current_hp > 0:
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
