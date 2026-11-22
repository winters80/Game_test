"""
The world clock: game time from the turn counter.

One turn is ``MINUTES_PER_TURN`` minutes of world time (an ordinary menu
choice). Longer activities pass more turns through ``core/time_flow.pass_time``
so the world (bots, lairs, buffs, quests) keeps moving while you sleep or
fight through a Gate. Turn 0 is Day 1, ``START_HOUR``:00.
"""
from __future__ import annotations

from config import DAY_START_HOUR, MINUTES_PER_TURN, NIGHT_START_HOUR, START_HOUR

TURNS_PER_HOUR = max(1, 60 // MINUTES_PER_TURN)
TURNS_PER_DAY = 24 * TURNS_PER_HOUR


def now(turn: int) -> tuple[int, int, int]:
    """(day, hour, minute) at this turn; day 1 is the first day."""
    minutes = START_HOUR * 60 + max(0, turn) * MINUTES_PER_TURN
    day, rest = divmod(minutes, 24 * 60)
    return day + 1, rest // 60, rest % 60


def phase(turn: int) -> str:
    """'day' between DAY_START_HOUR and NIGHT_START_HOUR, else 'night'
    (NPC schedules use this)."""
    _, hour, _ = now(turn)
    return "day" if DAY_START_HOUR <= hour < NIGHT_START_HOUR else "night"


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def label(turn: int) -> str:
    day, hour, minute = now(turn)
    return f"Day {day}, {hour:02d}:{minute:02d} ({part_of_day(hour)})"


def hours(h: float) -> int:
    """Turns in ``h`` hours."""
    return max(1, round(h * TURNS_PER_HOUR))


def duration(turns: int) -> str:
    """'about 3 hours' / 'about 2 days' for a number of turns."""
    mins = max(0, turns) * MINUTES_PER_TURN
    if mins < 90:
        return f"about {max(10, round(mins / 10) * 10)} minutes"
    if mins < 36 * 60:
        return f"about {round(mins / 60)} hours"
    return f"about {round(mins / 1440)} days"
