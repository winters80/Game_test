"""
Status effects and boss abilities in combat. Pure logic, no rendering.

Statuses (``combat_system.STATUS_EFFECTS``) live in a plain ``{name: turns}``
dict: ``Enemy.statuses`` for enemies, and a combat-local dict for the player
(statuses end with the fight).

- poison / bleed / burn: damage each round
- stun: the next action is lost (bosses of rank C and up resist half the time);
  a stunned combatant can't be stunned again for ``STUN_GUARD_ROUNDS`` rounds
- frozen: the player dodges less; an enemy hits 2 softer

Who inflicts them:
- enemies with ``status_on_hit`` roll it on every hit that lands
- the player's spells by ``spell_type`` (fire → burn, ice → frozen, arcane →
  stun sometimes), DEBUFF effects → stun, and a TRIGGERED skill whose name says
  venom / poison → poison

Boss abilities (``Enemy.abilities``, authored on lair / gate enemy templates):
each has ``kind`` heavy | status | heal | summon | drain, fires every
``every`` turns of that enemy, and a heavy blow is announced one turn ahead
so the player can react. ``enrage_below`` (share of max HP) makes the enemy
hit 25% harder once, when its HP first drops below it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entities.player import Player
    from entities.skill import Skill, SkillRegistry
    from systems.combat_system import Enemy
    from systems.passive_system import TriggerFiring

FROZEN_DODGE_PENALTY = 0.024     # = 3 AGI of dodge
FROZEN_ATTACK_PENALTY = 2
ENRAGE_MULT = 1.25
MAX_SUMMONS_ALIVE = 2
STUN_RESIST_RANKS = {"C", "B", "A", "S"}
STUN_GUARD_ROUNDS = 2            # after a stun, no new stun for this many rounds


def _info(effect: str) -> dict:
    from systems.combat_system import STATUS_EFFECTS
    return STATUS_EFFECTS.get(effect, {})


def inflict(bag: dict, effect: str, rank: str = "", rng: random.Random | None = None) -> bool:
    """Add (or refresh) a status. Returns False if it was resisted."""
    rng = rng or random
    if effect == "stun" and (bag.get("_stun_guard") or (rank in STUN_RESIST_RANKS and rng.random() < 0.5)):
        return False
    if not _info(effect):
        return False
    bag[effect] = max(bag.get(effect, 0), _info(effect).get("duration", 2))
    return True


def tick(bag: dict, hp: int) -> tuple[int, list[tuple[str, int]]]:
    """End of round: damage over time and countdown. Returns (new_hp, [(effect, damage)])."""
    hits = []
    for effect in list(bag):
        if effect == "_stun_guard":
            bag[effect] -= 1
            if bag[effect] <= 0:
                del bag[effect]
            continue
        if effect.startswith("_"):            # ward / evade: spent by hits, not by time
            continue
        dot = _info(effect).get("damage_per_turn", 0)
        if dot and hp > 0:
            hp = max(0, hp - dot)
            hits.append((effect, dot))
        if effect == "stun":
            continue            # stun is spent by losing a turn, not by time
        bag[effect] -= 1
        if bag[effect] <= 0:
            del bag[effect]
    return hp, hits


def consume_stun(bag: dict) -> bool:
    """True (and the stun is spent) if this combatant loses its action."""
    if bag.get("stun"):
        del bag["stun"]
        bag["_stun_guard"] = STUN_GUARD_ROUNDS     # no stun-locking: immune for a little while
        return True
    return False


def skill_status(skill: "Skill | None") -> tuple[str, float] | None:
    """(status, chance) a player skill inflicts on the target, if any."""
    if skill is None:
        return None
    st = (skill.spell_type or "").lower()
    if st == "fire":
        return ("burn", 0.5)
    if st == "ice":
        return ("frozen", 0.5)
    if any(getattr(e.effect_type, "value", e.effect_type) == "debuff" for e in skill.effects):
        return ("stun", 0.35)
    if st == "arcane":
        return ("stun", 0.15)
    return None


def is_defensive(skill) -> bool:
    """An ACTIVE skill whose effects are SHIELD / BUFF only (Mana Shield,
    Shadow Step): it protects the caster instead of hitting the target."""
    kinds = {getattr(e.effect_type, "value", e.effect_type) for e in getattr(skill, "effects", [])}
    return bool(kinds) and kinds <= {"shield", "buff"}


def apply_defensive(skill, player, bag: dict) -> str:
    """SHIELD → a ward that absorbs that much damage this fight; BUFF → evade
    the next 1-2 attacks. Returns the combat-log line."""
    from systems.skill_system import calculate_skill_damage
    value = calculate_skill_damage(skill, player)
    kinds = {getattr(e.effect_type, "value", e.effect_type) for e in skill.effects}
    parts = []
    if "shield" in kinds:
        bag["_ward"] = bag.get("_ward", 0) + value
        parts.append(f"a ward that absorbs {bag['_ward']} damage")
    if "buff" in kinds:
        charges = 2 if value >= 20 else 1
        bag["_evade"] = bag.get("_evade", 0) + charges
        parts.append(f"{charges} evade{'s' if charges > 1 else ''}")
    return f"You cast {skill.name}: {' and '.join(parts)}."


def absorb(bag: dict | None, damage: int) -> tuple[int, str]:
    """Evade or ward against an incoming hit. Returns (damage left, note)."""
    if not bag or damage <= 0:
        return damage, ""
    if bag.get("_evade"):
        bag["_evade"] -= 1
        if bag["_evade"] <= 0:
            del bag["_evade"]
        return 0, "evaded"
    ward = bag.get("_ward", 0)
    if ward:
        used = min(ward, damage)
        bag["_ward"] = ward - used
        if bag["_ward"] <= 0:
            del bag["_ward"]
        return damage - used, f"ward absorbs {used}"
    return damage, ""


def trigger_statuses(fired: list["TriggerFiring"]) -> list[tuple[str, float]]:
    """Statuses from TRIGGERED skills that just fired (Venom Strike → poison)."""
    out = []
    for f in fired:
        name = f.skill_name.lower()
        if "venom" in name or "poison" in name:
            out.append(("poison", 0.5))
    return out


@dataclass
class EnemyTurn:
    """What one enemy did. ``lines`` are (text, style) for the renderer."""
    damage: int = 0
    lines: list[tuple[str, str]] = field(default_factory=list)
    summoned: list = field(default_factory=list)
    fired: list = field(default_factory=list)
    cues: list[str] = field(default_factory=list)       # sound cues: enrage, summon, boss_warning


def _due(enemy: "Enemy") -> dict | None:
    for ab in enemy.abilities:
        if enemy.turns_taken > 0 and enemy.turns_taken % max(2, int(ab.get("every", 3))) == 0:
            return ab
    return None


def _upcoming(enemy: "Enemy") -> dict | None:
    nxt = enemy.turns_taken + 1
    for ab in enemy.abilities:
        if ab.get("kind") == "heavy" and nxt % max(2, int(ab.get("every", 3))) == 0:
            return ab
    return None


# ── Tactics: a boss picks its move from the fight's state (core/combat_handler
# asks AIService.decide while the player chooses; these stay pure). ─────────────

BASIC = {"kind": "basic", "name": "a basic attack"}


def tactic_options(enemy: "Enemy", enemies: list | None = None) -> list[dict]:
    """Moves a boss may choose this turn. Heavy blows keep their announced
    schedule (the player was warned), so they are never offered here; the
    other abilities need their ``every`` cooldown since their last use, a heal
    needs missing HP, a summon needs room."""
    if not enemy.abilities:
        return []
    nxt = enemy.turns_taken + 1
    out = [BASIC]
    for ab in enemy.abilities:
        kind = ab.get("kind")
        if kind == "heavy":
            continue
        every = max(2, int(ab.get("every", 3)))
        last = enemy.ability_last.get(ab.get("name", kind), -10**6)
        if nxt - last < every:
            continue
        if kind == "heal" and enemy.current_hp > enemy.max_hp * 0.7:
            continue
        if kind == "summon" and enemies is not None and \
                sum(1 for e in enemies if e.is_alive and e.summoned) >= MAX_SUMMONS_ALIVE:
            continue
        out.append(ab)
    return out if len(out) > 1 else []


def heavy_due(enemy: "Enemy") -> bool:
    """True if this turn is a scheduled (announced) heavy blow."""
    nxt = enemy.turns_taken + 1
    return any(ab.get("kind") == "heavy" and nxt % max(2, int(ab.get("every", 3))) == 0
               for ab in enemy.abilities)


def tactic_question(enemy: "Enemy", player: "Player", player_statuses: dict, options: list[dict]) -> tuple[str, list[str]]:
    """The decision to ask, and one label per option."""
    mine = f"{enemy.current_hp}/{enemy.max_hp} HP" + (", enraged" if enemy.enraged else "")
    theirs = f"{player.current_hp}/{player.max_hp} HP"
    if player_statuses:
        theirs += ", " + ", ".join(sorted(k for k in player_statuses if not k.startswith("_")))
    labels = []
    for ab in options:
        k = ab.get("kind")
        what = {"basic": "a plain attack", "heal": "heal yourself", "summon": f"call {ab.get('summon', 'help')}",
                "drain": "an attack that steals HP", "status": f"an attack that inflicts {ab.get('status', 'a status')}"
                }.get(k, k)
        labels.append(what if k == "basic" else f"{ab.get('name', k)}: {what}")
    q = (f"You are {enemy.name} ({mine}) fighting an adventurer ({theirs}). "
         "Which move gives you the best chance to win this turn?")
    return q, labels


def enemy_turn(
    enemy: "Enemy", player: "Player", player_statuses: dict,
    skill_registry: "SkillRegistry | None" = None,
    enemies: list | None = None, rng: random.Random | None = None,
    chosen: dict | None = None,
) -> EnemyTurn:
    """One enemy's action: stunned, enraged, signature ability, or a basic
    attack (with its on-hit status)."""
    from systems.combat_system import enemy_attack, spawn_enemy

    rng = rng or random
    out = EnemyTurn()
    if consume_stun(enemy.statuses):
        out.lines.append((f"{enemy.name} is stunned and loses its turn!", "gold"))
        return out
    enemy.turns_taken += 1

    if enemy.enrage_below and not enemy.enraged and enemy.current_hp < enemy.max_hp * enemy.enrage_below:
        enemy.enraged = True
        enemy.attack = round(enemy.attack * ENRAGE_MULT)
        out.lines.append((f"{enemy.name} becomes ENRAGED!", "system_warning"))
        out.cues.append("enrage")

    frozen = FROZEN_ATTACK_PENALTY if enemy.statuses.get("frozen") else 0
    ab = _due(enemy)
    # A tactical choice replaces the cycle, except on an announced heavy turn.
    if chosen is not None and not (ab and ab.get("kind") == "heavy"):
        ab = None if chosen.get("kind") == "basic" else chosen
    if ab:
        enemy.ability_last[ab.get("name", ab.get("kind"))] = enemy.turns_taken
    kind = (ab or {}).get("kind")
    name = (ab or {}).get("name", "")

    def _hit(mult: float = 1.0) -> int:
        base = enemy.attack
        enemy.attack = max(1, round(base * mult) - frozen)
        try:
            return enemy_attack(enemy, player, skill_registry, out.fired, player_statuses)
        finally:
            enemy.attack = base

    if kind == "heal":
        amount = max(1, round(enemy.max_hp * float(ab.get("power", 0.15))))
        enemy.current_hp = min(enemy.max_hp, enemy.current_hp + amount)
        out.lines.append((f"{enemy.name} uses {name} and recovers {amount} HP!", "system_warning"))
    elif kind == "summon" and enemies is not None:
        alive_summons = sum(1 for e in enemies if e.is_alive and e.summoned)
        new = spawn_enemy(ab.get("summon", "")) if alive_summons < MAX_SUMMONS_ALIVE else None
        if new is not None:
            new.summoned = True
            enemies.append(new)
            out.summoned.append(new)
            out.lines.append((f"{enemy.name} uses {name}: {new.name} joins the fight!", "system_warning"))
            out.cues.append("summon")
        else:
            kind = None
    if kind in ("heavy", "drain", "status"):
        mult = float(ab.get("power", 1.8)) if kind == "heavy" else 1.0
        out.damage = _hit(mult)
        verb = f"uses {name}"
        if out.damage == 0:
            out.lines.append((f"{enemy.name} {verb} — you dodge!", "miss"))
        else:
            out.lines.append((f"{enemy.name} {verb} for {out.damage} damage!", "damage"))
            if kind == "drain":
                heal = max(1, round(out.damage * float(ab.get("power", 0.5))))
                enemy.current_hp = min(enemy.max_hp, enemy.current_hp + heal)
                out.lines.append((f"{enemy.name} drinks {heal} HP from you.", "system_warning"))
            if kind == "status" and ab.get("status") and inflict(player_statuses, ab["status"]):
                out.lines.append((f"You are {_info(ab['status']).get('message', ab['status'])}!", "system_warning"))
    elif kind is None:
        out.damage = _hit()
        out.lines.append((f"{enemy.name} strikes you for {out.damage}." if out.damage
                          else f"{enemy.name} attacks — you dodge!", "damage" if out.damage else "miss"))
        if out.damage and enemy.status_on_hit:
            eff, chance = enemy.status_on_hit
            if rng.random() < chance and inflict(player_statuses, eff):
                out.lines.append((f"You are {_info(eff).get('message', eff)}!", "system_warning"))

    warn = _upcoming(enemy)
    if warn and enemy.is_alive:
        out.lines.append((f"⚠ {enemy.name} is gathering itself for {warn.get('name', 'something big')}!", "system_warning"))
        out.cues.append("boss_warning")
    return out


def tick_enemy(enemy: "Enemy") -> list[tuple[str, int]]:
    hp, hits = tick(enemy.statuses, enemy.current_hp)
    enemy.current_hp = hp
    return hits


def status_label(bag: dict) -> str:
    parts = [f"{k} {v}" if k != "stun" else "stunned" for k, v in bag.items() if not k.startswith("_")]
    if bag.get("_ward"):
        parts.append(f"ward {bag['_ward']}")
    if bag.get("_evade"):
        parts.append(f"evade ×{bag['_evade']}")
    return ", ".join(parts)
