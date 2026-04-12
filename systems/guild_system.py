"""
Guild System

Handles:
  - Joining guilds (standing check, requirements check)
  - Ranking up within a guild
  - Applying guild perks to player
  - Checking if player can access guild-gated content
  - Tick: NPC competitors bid on auction, guilds react to player actions
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.guild import GuildDefinition, GuildRegistry
    from core.state_manager import GameState


def can_join_guild(player: "Player", guild: "GuildDefinition") -> tuple[bool, str]:
    """Check if player meets joining requirements. Returns (can_join, reason)."""
    req = guild.join_requires
    if not req:
        return True, ""

    if "min_stats" in req:
        for stat, threshold in req["min_stats"].items():
            val = getattr(player.stats, stat, 0)
            if val < threshold:
                return False, f"Requires {stat} {threshold} (you have {val})"

    if "flags" in req:
        for flag in req["flags"]:
            if not player.has_flag(flag):
                return False, f"Missing required standing or introduction"

    if "alignment_min" in req:
        if player.alignment < req["alignment_min"]:
            return False, f"Your alignment is too low for this guild"

    if "alignment_max" in req:
        if player.alignment > req["alignment_max"]:
            return False, f"Your alignment is too high for this guild"

    return True, ""


def join_guild(
    player: "Player",
    guild: "GuildDefinition",
    state: "GameState",
) -> tuple[bool, str]:
    """Attempt to join guild. Returns (success, message)."""
    if guild.guild_id in player.guild_memberships:
        return False, f"You are already a member of {guild.name}."

    can, reason = can_join_guild(player, guild)
    if not can:
        return False, reason

    first_rank = guild.ranks[0] if guild.ranks else None
    rank_id = first_rank.rank_id if first_rank else "recruit"
    player.guild_memberships[guild.guild_id] = rank_id

    # Initialize guild standing in world_db if not present
    if state.world_db:
        standing, _ = state.world_db.get_faction_standing(f"guild_{guild.guild_id}")
        if standing == 0.0:
            state.world_db.update_faction_standing(f"guild_{guild.guild_id}", 0.0)

    bus.publish(Event("GUILD_JOINED", {
        "guild_id": guild.guild_id,
        "guild_name": guild.name,
        "rank_id": rank_id,
    }))

    return True, f"You have joined the {guild.name} as {first_rank.name if first_rank else 'Recruit'}."


def get_guild_standing(player: "Player", guild_id: str, state: "GameState") -> float:
    """Get player's standing with a guild."""
    if state.world_db:
        standing, _ = state.world_db.get_faction_standing(f"guild_{guild_id}")
        return standing
    return 0.0


def update_guild_standing(
    guild_id: str,
    delta: float,
    state: "GameState",
    guild_registry: "GuildRegistry",
) -> None:
    """Update guild standing and check for rank changes."""
    if not state.world_db:
        return

    state.world_db.update_faction_standing(f"guild_{guild_id}", delta)
    new_standing, _ = state.world_db.get_faction_standing(f"guild_{guild_id}")

    # Check for rank up
    guild = guild_registry.get(guild_id)
    if guild and guild_id in state.player.guild_memberships:
        current_rank_id = state.player.guild_memberships[guild_id]
        new_rank = guild.rank_for_standing(new_standing)
        if new_rank.rank_id != current_rank_id:
            old_rank = guild.get_rank(current_rank_id)
            state.player.guild_memberships[guild_id] = new_rank.rank_id
            _apply_rank_perks(state.player, guild, new_rank)
            bus.publish(Event("GUILD_RANK_CHANGED", {
                "guild_id": guild_id,
                "guild_name": guild.name,
                "old_rank": old_rank.name if old_rank else current_rank_id,
                "new_rank": new_rank.name,
            }))


def _apply_rank_perks(player: "Player", guild: "GuildDefinition", rank: object) -> None:
    """Apply all perks available at the new rank."""
    # Get all perks at or below this rank
    rank_order = [r.rank_id for r in guild.ranks]
    rank_idx = rank_order.index(rank.rank_id) if rank.rank_id in rank_order else 0

    for perk in guild.perks:
        perk_rank_idx = rank_order.index(perk.rank_required) if perk.rank_required in rank_order else 999
        if perk_rank_idx <= rank_idx:
            # Apply stat bonuses
            for stat, bonus in perk.stat_bonuses.items():
                current = getattr(player.stats, stat, 0)
                setattr(player.stats, stat, current + bonus)
            # Apply special flags
            if perk.special_flag:
                player.set_flag(perk.special_flag)
            # Grant skills
            for skill_id in perk.skill_unlocks:
                if skill_id not in player.skills:
                    player.skills.append(skill_id)
                    bus.publish(Event("SKILL_ACQUIRED", {
                        "skill_id": skill_id,
                        "skill_name": skill_id.replace("_", " ").title(),
                        "rarity": "RARE",
                    }))


def get_active_perks(player: "Player", guild: "GuildDefinition") -> list:
    """Return all perks the player currently qualifies for in this guild."""
    if guild.guild_id not in player.guild_memberships:
        return []
    current_rank_id = player.guild_memberships[guild.guild_id]
    rank_order = [r.rank_id for r in guild.ranks]
    rank_idx = rank_order.index(current_rank_id) if current_rank_id in rank_order else 0
    return [
        p for p in guild.perks
        if p.rank_required in rank_order
        if rank_order.index(p.rank_required) <= rank_idx
    ]


def get_guild_discount(player: "Player", guild: "GuildDefinition") -> int:
    """Return the highest discount_pct the player has from guild perks."""
    perks = get_active_perks(player, guild)
    return max((p.discount_pct for p in perks), default=0)
