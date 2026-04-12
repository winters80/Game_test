"""
Lives System

Handles:
  - Player death events: deduct a life, apply configured penalty, respawn
  - Death record logging to world_db SQLite
  - Game-over detection (all lives spent)
  - Life-token grants (from auction wins, quest rewards, etc.)
  - Narrative summaries for AI prompt injection
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import Event, bus
from config import (
    DEATH_PENALTY_MODE,
    DEATH_XP_PENALTY_PCT,
    DEATH_ALIGNMENT_PENALTY,
    MAX_LIVES,
)

if TYPE_CHECKING:
    from core.state_manager import GameState


# ── Death handling ────────────────────────────────────────────────────────────

def handle_death(
    state: "GameState",
    cause: str,
    zone_id: str,
) -> bool:
    """
    Process a player death event.

    Returns:
        True  — player still has lives remaining; they will respawn.
        False — all lives spent; game over.
    """
    player = state.player

    # Record in world_db before decrementing so lives_remaining reflects
    # the value BEFORE this death.
    if state.world_db:
        state.world_db.record_death(
            cause=cause,
            zone_id=zone_id,
            turn_number=state.turn_number,
            level_at_death=player.level,
            alignment_at_death=player.alignment,
            lives_remaining=max(0, player.lives_remaining - 1),
        )

    # Deduct life
    player.lives_remaining = max(0, player.lives_remaining - 1)
    player.lives_used += 1

    # Apply death penalty
    _apply_death_penalty(player, cause)

    bus.publish(Event("PLAYER_DIED", {
        "lives_remaining": player.lives_remaining,
        "lives_used":      player.lives_used,
        "cause":           cause,
        "zone_id":         zone_id,
    }))

    if player.lives_remaining <= 0:
        bus.publish(Event("GAME_OVER", {
            "cause":        cause,
            "total_deaths": player.lives_used,
            "final_level":  player.level,
        }))
        return False

    # Respawn at last safe zone with partial resources
    _respawn(player, state)
    return True


def _apply_death_penalty(player, cause: str) -> None:
    """Apply the death penalty configured in config.DEATH_PENALTY_MODE."""
    if DEATH_PENALTY_MODE == "xp":
        penalty = int(player.experience * DEATH_XP_PENALTY_PCT)
        player.experience = max(0, player.experience - penalty)
    elif DEATH_PENALTY_MODE == "alignment":
        player.shift_alignment(DEATH_ALIGNMENT_PENALTY)
    elif DEATH_PENALTY_MODE == "item":
        # Drop the last item in inventory (if any)
        if player.inventory:
            player.inventory.pop()
    # DEATH_PENALTY_MODE == "none" → do nothing


def _respawn(player, state: "GameState") -> None:
    """Restore the player to their last safe zone with partial HP/MP."""
    player.current_hp = max(1, player.max_hp // 3)
    player.current_mp = max(0, player.max_mp // 2)
    state.current_scene_id = player.last_safe_zone_id
    state.current_node_id  = "root"


# ── Life token grants ─────────────────────────────────────────────────────────

def grant_life_token(state: "GameState", source: str = "reward") -> bool:
    """
    Add one extra life (from auction win, quest reward, etc.).
    Returns False and does nothing if already at MAX_LIVES.
    """
    player = state.player
    if player.lives_remaining >= MAX_LIVES:
        return False

    player.lives_remaining += 1
    bus.publish(Event("LIFE_GRANTED", {
        "lives_remaining": player.lives_remaining,
        "source":          source,
    }))
    return True


# ── Queries ───────────────────────────────────────────────────────────────────

def is_game_over(state: "GameState") -> bool:
    return state.player.lives_remaining <= 0


def death_count(state: "GameState") -> int:
    """Total number of times the player has died this playthrough."""
    if state.world_db:
        return state.world_db.get_death_count()
    return state.player.lives_used


def get_death_narrative(state: "GameState") -> str:
    """
    Short narrative summary of the player's death history.
    Injected into AI prompts to colour generated content.
    """
    count = death_count(state)
    if count == 0:
        return ""
    lives_left = state.player.lives_remaining
    plural = "time" if count == 1 else "times"
    return (
        f"This adventurer has died {count} {plural} and has "
        f"{lives_left} {'life' if lives_left == 1 else 'lives'} remaining."
    )
