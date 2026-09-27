"""
Passive skill aggregation + triggered skill firing.

Hand-crafted PASSIVE skills used to be 100% decorative — Iron Skin, Evasion,
Weapon Mastery, Divine Ward, Tracking, Persuasion, Intimidation, Foraging,
Chaotic Aura, Void Infusion, Trap Detection all appeared in the player's
skill list but had zero effect on gameplay because combat just filtered
them out. This module gives them meaning.

## Design

Every PASSIVE skill the player owns contributes to a ``PassiveModifiers``
bundle. Bundles are computed on demand from ``player.skills`` and the
registry; they're never cached, so adding/removing skills is reflected
immediately.

### Stacking rules

Different effect kinds stack differently — additive stacking on percentage-
based bonuses (dodge, crit) would trivialise combat, so:

| Bonus type        | Stack rule | Hard cap |
|-------------------|------------|----------|
| Attack bonus      | Additive   | +20 dmg  |
| Defense bonus     | Additive   | +15 dmg reduction |
| Magic defense     | Additive   | +10 dmg reduction |
| Bonus damage on hit | Additive | +30 dmg  |
| Dodge chance      | Additive   | 40% max  |
| Crit chance       | Additive   | 30% max  |
| Utility tags      | Set union  | (no cap, presence-only) |

The caps prevent a player who happens to have stacked four +END passives
from becoming unkillable. Adding new bonus types means picking a stack
rule + cap; do not add an uncapped percentage bonus.

### Skill → bonus mapping

The mapping is keyword-based on the skill's `name` / `description` /
`spell_type` so content authors don't need to learn a new effect schema.
The default behaviour for any unmatched PASSIVE skill is to fall back to
a generic stat bonus (the BUFF effect on the skill itself, if any).

### Triggered skills

`try_fire_trigger(player, event_name, ...)` is called by combat at the
relevant moments. Returns the bonus damage / healing the trigger
produced, so the caller can fold it into the combat log.

| Event       | Fired from                     | When                                        | Value used as |
|-------------|--------------------------------|---------------------------------------------|---------------|
| `on_attack` | `combat_system.player_attack`  | Player makes a basic attack                 | bonus damage  |
| `on_hit`    | `combat_system.player_attack`  | A basic attack lands (currently always)     | bonus damage  |
| `on_kill`   | `combat_system.player_attack`  | The basic attack drops the enemy            | healing       |
| `on_low_hp` | `combat_system.enemy_attack`   | An enemy hit takes HP from >= `LOW_HP_TRIGGER_THRESHOLD` of max to below it (player still alive) | healing |

`on_attack` and `on_hit` are distinct events that fire at the same point
today because basic attacks can't miss; if a miss mechanic is added,
`on_hit` should only fire on the landed branch.

A skill's `trigger_condition` is matched exactly. It may list several
events separated by commas (`"on_hit, on_kill"`); substrings never match.

TRIGGERED skills honour `cooldown_turns`: once one fires, it is put on
the normal per-combat cooldown and skipped until it expires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from entities.enums import EffectType, SkillType

if TYPE_CHECKING:
    from entities.player import Player
    from entities.skill import Skill, SkillRegistry


# ── Hard caps on stacked passive bonuses ────────────────────────────────────

MAX_ATTACK_BONUS         = 20      # additive damage on basic attacks
MAX_DEFENSE_BONUS        = 15      # additive flat damage reduction
MAX_MAGIC_DEFENSE_BONUS  = 10
MAX_BONUS_DAMAGE_ON_HIT  = 30      # additional damage on every basic attack
MAX_DODGE_CHANCE         = 0.40    # 40%
MAX_CRIT_CHANCE          = 0.30    # 30%


@dataclass
class PassiveModifiers:
    """Aggregate effect of every PASSIVE skill the player owns."""
    attack_bonus: int = 0
    defense_bonus: int = 0
    magic_defense_bonus: int = 0
    bonus_damage_on_hit: int = 0
    dodge_chance: float = 0.0
    crit_chance: float = 0.0
    utility_tags: set[str] = field(default_factory=set)


# ── Keyword → bonus mapping ─────────────────────────────────────────────────
#
# A skill's name/description is searched for these substrings (lower-cased).
# Order matters only when two patterns would match the same skill — the
# first match wins. Generic stat fallback runs last for anything unmatched.

_DEFENSE_KEYWORDS  = ("iron skin", "armor", "tough skin", "stoneskin")
_MDEFENSE_KEYWORDS = ("divine ward", "ward", "barrier", "magic resist")
_ATTACK_KEYWORDS   = ("weapon mastery", "weapon training", "swordsmanship")
_BONUS_DMG_KEYWORDS = ("void infusion", "elemental infusion", "imbued strike")
_DODGE_KEYWORDS    = ("evasion", "evade", "acrobat")
_CRIT_KEYWORDS     = ("chaotic aura", "lucky", "fortunate", "crit")

_UTILITY_KEYWORDS = {
    "tracking":         "tracking",
    "persuasion":       "persuasion",
    "intimidation":     "intimidation",
    "foraging":         "foraging",
    "trap detection":   "trap_detection",
    "inspect":          "inspect",
    "lockpicking":      "lockpicking",
}


# ── Public API ──────────────────────────────────────────────────────────────

def get_passive_modifiers(
    player: "Player", skill_registry: "SkillRegistry | None",
) -> PassiveModifiers:
    """Return the aggregated passive bonuses the player currently enjoys.

    Computed every call — passive bonuses are tiny so the cost is irrelevant
    compared to the simplicity of "just-look-at-the-current-skill-list".
    """
    mods = PassiveModifiers()
    if skill_registry is None:
        return mods

    for skill_id in player.skills:
        skill = skill_registry.get(skill_id)
        if skill is None or skill.skill_type != SkillType.PASSIVE:
            continue
        _apply_passive_skill(skill, player, mods)

    # Apply hard caps so stacked passives can't reach degenerate ratios.
    mods.attack_bonus        = min(mods.attack_bonus, MAX_ATTACK_BONUS)
    mods.defense_bonus       = min(mods.defense_bonus, MAX_DEFENSE_BONUS)
    mods.magic_defense_bonus = min(mods.magic_defense_bonus, MAX_MAGIC_DEFENSE_BONUS)
    mods.bonus_damage_on_hit = min(mods.bonus_damage_on_hit, MAX_BONUS_DAMAGE_ON_HIT)
    mods.dodge_chance        = min(mods.dodge_chance, MAX_DODGE_CHANCE)
    mods.crit_chance         = min(mods.crit_chance, MAX_CRIT_CHANCE)
    return mods


@dataclass
class TriggerFiring:
    """One TRIGGERED skill that fired — lets combat tell the player about it."""
    skill_id: str
    skill_name: str
    event: str
    value: int


def fire_triggers(
    player: "Player",
    event_name: str,
    skill_registry: "SkillRegistry | None",
) -> list[TriggerFiring]:
    """Fire any TRIGGERED skills bound to ``event_name``; return what fired.

    Each firing's ``value`` is damage for ``on_attack`` / ``on_hit`` and
    healing for ``on_kill`` / ``on_low_hp``. The caller decides how to
    apply it (add to attack damage, heal the player, etc.).

    Supported triggers right now: ``on_attack``, ``on_hit``, ``on_kill``,
    ``on_low_hp``. Anything else is silently ignored. Skills on cooldown
    are skipped; a skill with ``cooldown_turns > 0`` goes on cooldown
    when it fires.
    """
    if skill_registry is None:
        return []
    fired: list[TriggerFiring] = []
    for skill_id in player.skills:
        skill = skill_registry.get(skill_id)
        if skill is None or skill.skill_type != SkillType.TRIGGERED:
            continue
        if event_name not in parse_trigger_conditions(skill.trigger_condition):
            continue
        if player.skill_cooldowns.get(skill_id, 0) > 0:
            continue
        fired.append(TriggerFiring(
            skill_id, skill.name, event_name, _evaluate_trigger_value(skill, player),
        ))
        if skill.cooldown_turns > 0:
            player.skill_cooldowns[skill_id] = skill.cooldown_turns
    return fired


def try_fire_trigger(
    player: "Player",
    event_name: str,
    skill_registry: "SkillRegistry | None",
) -> int:
    """Like ``fire_triggers`` but returns only the summed value."""
    return sum(f.value for f in fire_triggers(player, event_name, skill_registry))


def parse_trigger_conditions(trigger_condition: str | None) -> set[str]:
    """Split a ``trigger_condition`` into exact event names.

    ``"on_hit, ON_KILL"`` → ``{"on_hit", "on_kill"}``. ``None`` / ``""`` → empty.
    """
    if not trigger_condition:
        return set()
    return {part.strip().lower() for part in trigger_condition.split(",") if part.strip()}


# ── Internal: per-skill application ─────────────────────────────────────────

def _apply_passive_skill(
    skill: "Skill", player: "Player", mods: PassiveModifiers,
) -> None:
    """Translate a single PASSIVE skill into bonus contributions."""
    blob = f"{skill.name} {skill.description}".lower()

    # Utility tags — presence only, no numeric bonus
    for kw, tag in _UTILITY_KEYWORDS.items():
        if kw in blob:
            mods.utility_tags.add(tag)

    # Combat bonuses — first matching category wins so a single skill
    # produces one bonus type, not several.
    if any(k in blob for k in _DEFENSE_KEYWORDS):
        mods.defense_bonus += _bonus_from_skill(skill, player)
        return
    if any(k in blob for k in _MDEFENSE_KEYWORDS):
        mods.magic_defense_bonus += _bonus_from_skill(skill, player)
        return
    if any(k in blob for k in _ATTACK_KEYWORDS):
        mods.attack_bonus += _bonus_from_skill(skill, player)
        return
    if any(k in blob for k in _BONUS_DMG_KEYWORDS):
        mods.bonus_damage_on_hit += _bonus_from_skill(skill, player)
        return
    if any(k in blob for k in _DODGE_KEYWORDS):
        mods.dodge_chance += _dodge_from_skill(skill, player)
        return
    if any(k in blob for k in _CRIT_KEYWORDS):
        mods.crit_chance += _crit_from_skill(skill, player)
        return

    # ── Generic fallback ──────────────────────────────────────────────────
    # Any PASSIVE skill with a BUFF or DAMAGE effect that didn't match a
    # keyword still contributes generically. This is what gives AI-generated
    # passive skills meaning — the LLM can invent novel names and we'll
    # still apply *something* useful based on the effect_type.
    for eff in skill.effects:
        amt = int(_bonus_from_effect(eff, player))
        if eff.effect_type == EffectType.BUFF:
            mods.attack_bonus += amt
        elif eff.effect_type == EffectType.SHIELD:
            mods.defense_bonus += amt
        elif eff.effect_type == EffectType.DAMAGE:
            mods.bonus_damage_on_hit += amt


def _bonus_from_skill(skill: "Skill", player: "Player") -> int:
    """Compute additive integer bonus from a skill's effects."""
    if not skill.effects:
        # No explicit effect — give a small static bonus by rarity so
        # decorative-looking passives still do something.
        return _rarity_default(skill)
    total = 0.0
    for eff in skill.effects:
        total += _bonus_from_effect(eff, player)
    return max(1, int(total))


def _bonus_from_effect(effect, player: "Player") -> float:
    base = effect.base_value
    if effect.scaling_stat:
        stat_val = getattr(player.stats, effect.scaling_stat, 5)
        return base + stat_val * effect.scaling_coefficient
    return base


def _dodge_from_skill(skill: "Skill", player: "Player") -> float:
    """Convert an evasion-style passive to a flat dodge-chance addition."""
    base = 0.05  # +5% baseline for "Evasion" type
    if skill.effects:
        # If the skill scales (e.g. Evasion: buff=0.0(AGIx1.0)), use the
        # scaled value as a permille bump (AGI 10 → +1% dodge).
        for eff in skill.effects:
            if eff.scaling_stat:
                bonus = getattr(player.stats, eff.scaling_stat, 5) * eff.scaling_coefficient
                base += bonus * 0.001
    return base


def _crit_from_skill(skill: "Skill", player: "Player") -> float:
    """Convert a crit-style passive to a flat crit-chance addition."""
    base = 0.05
    if skill.effects:
        for eff in skill.effects:
            if eff.scaling_stat:
                bonus = getattr(player.stats, eff.scaling_stat, 5) * eff.scaling_coefficient
                base += bonus * 0.002  # LCK 10 + coeff 1.5 → +3% crit
    return base


def _rarity_default(skill: "Skill") -> int:
    """Fallback static bonus when a skill has no effects at all."""
    from entities.enums import Rarity
    return {
        Rarity.COMMON: 1, Rarity.UNCOMMON: 2, Rarity.RARE: 4,
        Rarity.EPIC: 6,  Rarity.LEGENDARY: 10,
    }.get(skill.rarity, 2)


def _evaluate_trigger_value(skill: "Skill", player: "Player") -> int:
    """How much value a TRIGGERED skill produces when it fires.

    Damage-typed triggers return damage. Heal-typed return heal amount.
    Buff-typed return a small bonus_damage value (so on-kill BUFF triggers
    feel like "you grow stronger after the kill").
    """
    if not skill.effects:
        return _rarity_default(skill)
    total = 0.0
    for eff in skill.effects:
        total += _bonus_from_effect(eff, player)
    return max(1, int(total))
