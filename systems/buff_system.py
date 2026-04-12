"""
Buff system — temporary stat bonuses from food, rest, potions, skills.

ActiveBuff stored in player.active_buffs as plain dicts for Pydantic compat:
    {"stat": "STR", "bonus": 2, "turns_remaining": 15, "source": "item_id_or_name"}

Stats supported: STR, INT, AGI, LCK, VIT, WIS, END
Special: stat="ALL" applies to all base stats.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entities.player import Player


def apply_buff(player: "Player", stat: str, bonus: int, duration: int, source: str = "") -> None:
    """Add a buff to the player. If an identical stat+source buff exists, refresh its duration."""
    stat = stat.upper()
    # Refresh if same stat + source already buffed
    for b in player.active_buffs:
        if b["stat"] == stat and b["source"] == source:
            b["bonus"] = max(b["bonus"], bonus)
            b["turns_remaining"] = max(b["turns_remaining"], duration)
            return
    player.active_buffs.append({
        "stat": stat,
        "bonus": bonus,
        "turns_remaining": duration,
        "source": source,
    })


def tick_buffs(player: "Player") -> list[str]:
    """
    Decrement buff durations. Remove expired buffs.
    Returns list of source names whose buffs just expired (for UI notification).
    """
    expired = []
    remaining = []
    for b in player.active_buffs:
        b["turns_remaining"] -= 1
        if b["turns_remaining"] <= 0:
            expired.append(b["source"])
        else:
            remaining.append(b)
    player.active_buffs = remaining
    return expired


def get_buff_bonus(player: "Player", stat: str) -> int:
    """Sum all active buff bonuses for a given stat (+ any ALL buffs)."""
    stat = stat.upper()
    total = 0
    for b in player.active_buffs:
        if b["stat"] == stat or b["stat"] == "ALL":
            total += b["bonus"]
    return total


def get_effective_stat(player: "Player", stat: str) -> int:
    """Return base stat + active buff bonuses."""
    base = getattr(player.stats, stat.upper(), 0)
    return base + get_buff_bonus(player, stat)


def summarize_buffs(player: "Player") -> list[str]:
    """Human-readable list of active buffs for UI display."""
    if not player.active_buffs:
        return []
    lines = []
    for b in player.active_buffs:
        stat_label = "All Stats" if b["stat"] == "ALL" else b["stat"]
        lines.append(f"+{b['bonus']} {stat_label} ({b['turns_remaining']}t) [{b['source']}]")
    return lines
