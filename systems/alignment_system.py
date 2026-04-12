"""
Alignment System

Tracks a player's moral/ethical position as a float from -100 to +100.
  +100 = Paragon of Light
     0 = Neutral
  -100 = Harbinger of Ruin

Design rules:
  - The float lives on Player.alignment. Never stored as a label.
  - Labels are derived at render time from config.ALIGNMENT_LABELS.
  - Shifts are applied through apply_alignment_shift() — never set directly.
  - A slow passive inertia pulls extreme values toward 0 over time (prevents
    permanent lock-in from early choices). Rate set in config.
  - Alignment gates in scenes use requires_alignment_min / requires_alignment_max.
  - Alignment affects: quest availability, NPC disposition, guild eligibility,
    faction reaction multipliers, some class unlock conditions.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config import (
    ALIGNMENT_INERTIA_RATE,
    ALIGNMENT_INERTIA_INTERVAL,
    ALIGNMENT_LABELS,
)
from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player


def apply_alignment_shift(player: "Player", delta: float, reason: str = "") -> float:
    """
    Shift player alignment by delta, clamped to [-100, +100].
    Fires an ALIGNMENT_SHIFTED event. Returns new alignment value.
    """
    old = player.alignment
    player.shift_alignment(delta)
    new = player.alignment

    if abs(new - old) > 0.01:
        bus.publish(Event("ALIGNMENT_SHIFTED", {
            "old": old,
            "new": new,
            "delta": delta,
            "reason": reason,
            "label": get_alignment_label(new),
        }))

    return new


def apply_inertia(player: "Player") -> None:
    """
    Nudge alignment slightly toward 0 (called every ALIGNMENT_INERTIA_INTERVAL turns).
    This prevents a character from being permanently locked at an extreme
    because of choices made in the first five minutes.
    """
    if abs(player.alignment) < 1.0:
        return
    direction = -1.0 if player.alignment > 0 else 1.0
    player.shift_alignment(direction * abs(ALIGNMENT_INERTIA_RATE))


def get_alignment_label(alignment: float) -> str:
    """Return the display label for an alignment float value."""
    for low, high, label in ALIGNMENT_LABELS:
        if low <= alignment <= high:
            return label
    return "Neutral"


def alignment_band(alignment: float) -> str:
    """
    Return a short band key used for filtering:
    'good', 'neutral', 'evil'
    """
    if alignment >= 20:
        return "good"
    elif alignment <= -20:
        return "evil"
    return "neutral"


def check_alignment_gate(player: "Player", min_val: float | None, max_val: float | None) -> tuple[bool, str]:
    """
    Check if the player's alignment meets optional min/max constraints.
    Returns (passes, reason_if_locked).
    """
    if min_val is not None and player.alignment < min_val:
        label = get_alignment_label(min_val)
        return False, f"Requires alignment ≥ {min_val:.0f} ({label})"
    if max_val is not None and player.alignment > max_val:
        label = get_alignment_label(max_val)
        return False, f"Requires alignment ≤ {max_val:.0f} ({label})"
    return True, ""


def should_apply_inertia(turn_count: int) -> bool:
    return turn_count > 0 and turn_count % ALIGNMENT_INERTIA_INTERVAL == 0
