"""
Faction System

Handles:
  - Faction standing updates with ripple effects (rival factions lose standing)
  - Rank evaluation and promotion events
  - Political ascension tracking
  - Alignment-faction relationship checks
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.faction import FactionDefinition, FactionRegistry
    from core.state_manager import GameState


RANK_ORDER = ["outsider", "known", "ally", "trusted", "champion", "commander",
              "aware", "asset", "operative", "inner_circle", "architect",
              "acquainted", "scholar", "associate", "fellow", "council_voice",
              "recognized", "favored", "noble_friend", "lord", "ruler_candidate",
              "sympathizer", "resistor", "breaker", "herald", "the_break",
              "marked", "initiate", "deathless", "eternal",
              "punter", "regular", "fence", "broker",
              "buyer", "member", "preferred", "patron"]

# How much rival faction standing is reduced per point gained with a faction
RIVAL_STANDING_PENALTY_RATE = 0.3


def get_standing(faction_id: str, state: "GameState") -> tuple[float, str]:
    """Get (standing_float, rank_id) for a faction."""
    if state.world_db:
        return state.world_db.get_faction_standing(faction_id)
    return 0.0, "outsider"


def update_standing(
    faction_id: str,
    delta: float,
    state: "GameState",
    faction_registry: "FactionRegistry",
    reason: str = "",
) -> None:
    """
    Update faction standing. Automatically penalizes rival factions
    and checks for rank changes.
    """
    if not state.world_db:
        return

    faction = faction_registry.get(faction_id)
    if not faction:
        return

    # Apply to this faction
    state.world_db.update_faction_standing(faction_id, delta)
    new_standing, old_rank = state.world_db.get_faction_standing(faction_id)

    # Cache on player
    state.player.faction_standing_cache[faction_id] = new_standing

    # Apply ripple to rivals (gaining standing with one loses standing with rivals)
    if delta > 0:
        for rival_id in faction.rival_factions:
            penalty = -(delta * RIVAL_STANDING_PENALTY_RATE)
            state.world_db.update_faction_standing(rival_id, penalty)
            rival_standing, _ = state.world_db.get_faction_standing(rival_id)
            state.player.faction_standing_cache[rival_id] = rival_standing

    # Check for rank change
    if faction:
        new_rank = faction.rank_for_standing(new_standing)
        current_rank_id = old_rank  # world_db returns current rank
        if new_rank.rank_id != current_rank_id:
            state.world_db.set_faction_rank(faction_id, new_rank.rank_id)
            bus.publish(Event("FACTION_RANK_CHANGED", {
                "faction_id": faction_id,
                "faction_name": faction.name,
                "old_rank": current_rank_id,
                "new_rank": new_rank.rank_id,
                "new_rank_name": new_rank.name,
            }))
            # Apply rank perks
            for perk_flag in new_rank.perks:
                state.player.set_flag(perk_flag)


def check_political_path(state: "GameState", faction_registry: "FactionRegistry") -> str | None:
    """
    Check if player qualifies for either ending path based on faction standings.
    Returns 'rule_the_system' | 'destroy_the_system' | None
    """
    if not state.world_db:
        return None

    # Path 1: Rule — need Verath Crown + Iron Vanguard at commander/champion level
    crown_standing, crown_rank = get_standing("verath_crown", state)
    vanguard_standing, vanguard_rank = get_standing("iron_vanguard_faction", state)

    if (state.player.has_flag("crown_ruler_candidate_flag") or crown_standing >= 90) and \
       (state.player.has_flag("iron_vanguard_command_flag") or vanguard_standing >= 75):
        return "rule_the_system"

    # Path 2: Destroy — need System Breakers + Shadow Network at high rank
    breakers_standing, _ = get_standing("system_breakers", state)
    shadow_standing, _ = get_standing("shadow_network_faction", state)

    if (state.player.has_flag("destroy_system_path_flag") or breakers_standing >= 80) and \
       (state.player.has_flag("fracture_truth_access") or shadow_standing >= 70):
        return "destroy_the_system"

    return None


def get_faction_summary(state: "GameState", faction_registry: "FactionRegistry") -> list[dict]:
    """Return display-ready faction standings for the UI."""
    summaries = []
    for faction in faction_registry.all():
        standing, rank_id = get_standing(faction.faction_id, state)
        if standing == 0.0 and rank_id == "outsider":
            continue  # Don't show factions player hasn't encountered
        summaries.append({
            "faction_id": faction.faction_id,
            "name": faction.name,
            "standing": standing,
            "rank_id": rank_id,
            "rank_name": faction.get_rank(rank_id).name if faction.get_rank(rank_id) else rank_id,
        })
    return summaries
