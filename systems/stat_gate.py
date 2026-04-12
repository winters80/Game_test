"""
Stat Gate System

Centralised evaluation of stat-based content gates. Four gate types:

  HARD        — Visible, locked if below threshold. Lock reason shown.
                Use for: stat-required moves, restricted shops, guild ranks.

  HIDDEN      — Option is invisible unless player meets threshold.
                Near-misses (within hidden_hint_threshold) show a vague hint.
                Use for: secret paths, hidden NPC dialogue, trap detection.

  SOFT        — Always available, but success is probabilistic.
                Success prob = base_chance + (player_stat - threshold) * coefficient.
                Use for: persuasion, pickpocketing, decoding lore.

  PROBABILITY — LCK-driven. Base chance + (LCK - threshold) * 2% per point.
                Use for: random beneficial events, loot quality rolls.

Stats supported: any Player.stats field (STR/INT/AGI/LCK/VIT/WIS/END),
plus derived "PERCEPTION" and special "ALIGNMENT" (uses player.alignment float).
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from pydantic import BaseModel

from entities.enums import GateType

if TYPE_CHECKING:
    from entities.player import Player


class StatGate(BaseModel):
    stat: str                             # "INT", "PERCEPTION", "LCK", "WIS", "ALIGNMENT", etc.
    threshold: float                      # float covers alignment; int stats work fine as floats
    gate_type: GateType = GateType.HARD
    hidden_hint_threshold: int = 0        # if player is within N of threshold, show hint text
    fail_message: str = ""               # override default lock message
    hint_text: str = ""                  # near-miss message for HIDDEN gates
    soft_base_chance: float = 0.3        # SOFT: base success probability (0.0–1.0)
    soft_coefficient: float = 0.05       # SOFT: success boost per stat point above floor


class GateResult(BaseModel):
    passed: bool
    gate_type: GateType = GateType.HARD
    lock_reason: str = ""
    hint_text: str = ""                  # non-empty = show hint even though locked
    success_probability: float = 1.0     # SOFT/PROBABILITY: chance of success (0.0–1.0)
    should_hide: bool = False            # HIDDEN gates below near-miss threshold


def _get_stat_value(player: "Player", stat: str) -> float:
    """Resolve any stat name (including derived) to a numeric value."""
    stat_upper = stat.upper()
    if stat_upper == "PERCEPTION":
        return float(player.perception)
    if stat_upper == "ALIGNMENT":
        return player.alignment
    # Standard stats
    return float(getattr(player.stats, stat_upper, 0))


def evaluate_gate(player: "Player", gate: StatGate) -> GateResult:
    value = _get_stat_value(player, gate.stat)
    threshold = gate.threshold
    deficit = threshold - value   # positive = player falls short

    if gate.gate_type == GateType.HARD:
        if value >= threshold:
            return GateResult(passed=True, gate_type=gate.gate_type)
        reason = gate.fail_message or f"Requires {gate.stat} ≥ {threshold:.0f} (you have {value:.0f})"
        return GateResult(passed=False, gate_type=gate.gate_type, lock_reason=reason)

    elif gate.gate_type == GateType.HIDDEN:
        if value >= threshold:
            return GateResult(passed=True, gate_type=gate.gate_type)
        # Near-miss: show hint but still locked
        if gate.hidden_hint_threshold > 0 and deficit <= gate.hidden_hint_threshold:
            hint = gate.hint_text or "You sense something here, but cannot quite grasp it..."
            return GateResult(passed=False, gate_type=gate.gate_type, hint_text=hint, should_hide=False)
        # Too far below: completely hidden
        return GateResult(passed=False, gate_type=gate.gate_type, should_hide=True)

    elif gate.gate_type == GateType.SOFT:
        # Always available, but success chance varies
        excess = max(0.0, value - threshold)
        prob = min(1.0, gate.soft_base_chance + excess * gate.soft_coefficient)
        return GateResult(passed=True, gate_type=gate.gate_type, success_probability=prob)

    elif gate.gate_type == GateType.PROBABILITY:
        # LCK-driven: 1% per LCK point above threshold, base 50%
        lck_bonus = max(0.0, value - threshold) * 0.02
        prob = min(1.0, 0.5 + lck_bonus)
        return GateResult(passed=True, gate_type=gate.gate_type, success_probability=prob)

    return GateResult(passed=True, gate_type=gate.gate_type)


def roll_soft_gate(result: GateResult) -> bool:
    """Roll for success on a SOFT or PROBABILITY gate result. True = success."""
    return random.random() < result.success_probability


def build_gate_from_dict(data: dict) -> StatGate | None:
    """
    Parse a gate dict from scene JSON.
    Accepts: {"stat": "INT", "threshold": 15, "gate_type": "hidden", "hint_text": "..."}
    Returns None if data is missing required fields.
    """
    if not data or "stat" not in data or "threshold" not in data:
        return None
    return StatGate(
        stat=data["stat"],
        threshold=float(data["threshold"]),
        gate_type=GateType(data.get("gate_type", "hard")),
        hidden_hint_threshold=int(data.get("hidden_hint_threshold", 0)),
        fail_message=data.get("fail_message", ""),
        hint_text=data.get("hint_text", ""),
        soft_base_chance=float(data.get("soft_base_chance", 0.3)),
        soft_coefficient=float(data.get("soft_coefficient", 0.05)),
    )
