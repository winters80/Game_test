"""
Auction House System

Handles:
  - Seeding initial listings (life tokens, rare items) when entering auction house
  - Player bidding and direct purchases
  - NPC counter-bidding (simulated competing guild bids)
  - Listing expiry / settlement (award items/lives to winning bidder)
  - Life token direct purchase at market rate
"""
from __future__ import annotations

import uuid
import random
from typing import TYPE_CHECKING

from core.event_bus import Event, bus
from config import (
    AUCTION_TURN_DURATION,
    AUCTION_LIFE_TOKEN_BASE_PRICE,
    AUCTION_LIFE_TOKEN_SCARCITY_MULT,
    MAX_LIVES,
)

if TYPE_CHECKING:
    from entities.guild import GuildRegistry
    from core.state_manager import GameState


# ── Seeding ───────────────────────────────────────────────────────────────────

def seed_initial_listings(state: "GameState", guild_registry: "GuildRegistry | None") -> None:
    """
    Called once when the player first enters the auction house.
    Seeds life-token listings and a couple of rare item listings from the two
    competing auction guilds.  Does nothing if listings already exist.
    """
    if not state.world_db:
        return

    if state.world_db.get_active_listings():
        return  # Already seeded this save

    current_turn = state.turn_number

    # Two life-token listings — one per auction guild
    for guild_id, starting_price in [
        ("golden_scales", 600),
        ("iron_hammer_exchange", 420),
    ]:
        listing_id = f"lt_{guild_id[:2]}_{str(uuid.uuid4())[:6]}"
        state.world_db.add_auction_listing(
            listing_id=listing_id,
            auction_guild=guild_id,
            item_id=None,
            item_type="life_token",
            quantity=1,
            starting_bid=starting_price,
            expires_turn=current_turn + AUCTION_TURN_DURATION,
        )

    # Rare item listings
    rare_items = [
        ("void_shard",     "rare",   300, "golden_scales"),
        ("aetheric_core",  "rare",   250, "iron_hammer_exchange"),
        ("health_potion",  "standard", 80, "golden_scales"),
    ]
    for item_id, item_type, price, guild_id in rare_items:
        listing_id = f"item_{item_id[:4]}_{str(uuid.uuid4())[:6]}"
        state.world_db.add_auction_listing(
            listing_id=listing_id,
            auction_guild=guild_id,
            item_id=item_id,
            item_type=item_type,
            quantity=1,
            starting_bid=price,
            expires_turn=current_turn + AUCTION_TURN_DURATION,
        )


# ── Pricing ───────────────────────────────────────────────────────────────────

def get_life_token_price(state: "GameState") -> int:
    """
    Life tokens get more expensive the more lives the player has already lost.
    Base price × scarcity_mult^lives_used
    """
    lives_lost = state.player.lives_used
    multiplier = AUCTION_LIFE_TOKEN_SCARCITY_MULT ** lives_lost
    return int(AUCTION_LIFE_TOKEN_BASE_PRICE * multiplier)


# ── Bidding ───────────────────────────────────────────────────────────────────

def place_player_bid(
    listing_id: str,
    amount: int,
    state: "GameState",
) -> tuple[bool, str]:
    """
    Player places a bid on an active listing.
    Gold is deducted immediately (escrow model).
    Returns (success, message).
    """
    if not state.world_db:
        return False, "Auction house unavailable."

    if state.player.gold < amount:
        return False, f"You only have {state.player.gold} Shards."

    listings = state.world_db.get_active_listings()
    listing = next((l for l in listings if l["listing_id"] == listing_id), None)
    if not listing:
        return False, "Listing not found or already expired."

    if amount <= listing["current_bid"]:
        return False, f"Bid must exceed the current bid of {listing['current_bid']} Shards."

    # Refund any previous player bid on this listing
    prev_bid = state.world_db._conn.execute(
        "SELECT MAX(bid_amount) as amt FROM auction_bids "
        "WHERE listing_id = ? AND bidder = 'player'",
        (listing_id,),
    ).fetchone()
    if prev_bid and prev_bid["amt"]:
        state.player.gold += prev_bid["amt"]

    state.player.gold -= amount
    success = state.world_db.place_bid(listing_id, "player", amount, state.turn_number)

    if success:
        return True, f"Bid of {amount} Shards placed. You are now the highest bidder."
    else:
        state.player.gold += amount
        return False, "Bid failed — you were outbid instantly."


def buy_life_token_direct(state: "GameState") -> tuple[bool, str]:
    """
    Buy a life token at current market price (bypasses auction).
    Available to players with golden_scales_patron flag or above.
    """
    price = get_life_token_price(state)

    if state.player.gold < price:
        return False, f"A life token costs {price:,} Shards. You have {state.player.gold:,}."

    if state.player.lives_remaining >= MAX_LIVES:
        return False, f"You already hold the maximum {MAX_LIVES} lives."

    state.player.gold -= price
    state.player.lives_remaining += 1

    bus.publish(Event("LIFE_TOKEN_PURCHASED", {
        "cost": price,
        "lives_remaining": state.player.lives_remaining,
    }))

    return True, (
        f"Life token purchased for {price:,} Shards. "
        f"Lives remaining: {state.player.lives_remaining}/{MAX_LIVES}."
    )


# ── Tick ──────────────────────────────────────────────────────────────────────

def tick_auction(state: "GameState") -> None:
    """
    Called every turn when auction_house feature is enabled.
    1. Expires old listings and settles them (award to highest bidder).
    2. Simulates NPC guild counter-bids on active listings.
    """
    if not state.world_db:
        return

    expired = state.world_db.expire_listings(state.turn_number)
    for listing in expired:
        _settle_listing(listing, state)

    # NPC counter-bidding every 5 turns
    if state.turn_number % 5 == 0:
        _simulate_npc_bids(state)


def _settle_listing(listing: dict, state: "GameState") -> None:
    """Award the listing to whoever holds the highest bid."""
    if not state.world_db:
        return

    top_bid = state.world_db._conn.execute(
        "SELECT bidder, bid_amount FROM auction_bids "
        "WHERE listing_id = ? ORDER BY bid_amount DESC LIMIT 1",
        (listing["listing_id"],),
    ).fetchone()

    if not top_bid:
        return  # No bids — listing just expires

    if top_bid["bidder"] != "player":
        # Player bid but lost — refund their last bid
        player_bid = state.world_db._conn.execute(
            "SELECT MAX(bid_amount) as amt FROM auction_bids "
            "WHERE listing_id = ? AND bidder = 'player'",
            (listing["listing_id"],),
        ).fetchone()
        if player_bid and player_bid["amt"]:
            state.player.gold += player_bid["amt"]
        return

    # Player won
    if listing["item_type"] == "life_token":
        if state.player.lives_remaining < MAX_LIVES:
            state.player.lives_remaining += 1
            bus.publish(Event("LIFE_TOKEN_PURCHASED", {
                "cost": top_bid["bid_amount"],
                "lives_remaining": state.player.lives_remaining,
            }))
    elif listing["item_id"]:
        state.player.add_item(listing["item_id"], listing.get("quantity", 1))
        bus.publish(Event("ITEM_FOUND", {
            "item_id": listing["item_id"],
            "item_name": listing["item_id"].replace("_", " ").title(),
            "rarity": listing["item_type"].upper() if listing["item_type"] != "standard" else "COMMON",
        }))


def _simulate_npc_bids(state: "GameState") -> None:
    """Competing guilds place counter-bids to drive up prices realistically."""
    if not state.world_db:
        return

    active = state.world_db.get_active_listings()
    npc_bidders = ["golden_scales_ai", "iron_hammer_ai", "wealthy_collector_ai"]

    for listing in active:
        if random.random() > 0.35:          # 35% chance per listing per tick
            continue
        npc = random.choice(npc_bidders)
        new_bid = int(listing["current_bid"] * random.uniform(1.04, 1.18))
        state.world_db.place_bid(listing["listing_id"], npc, new_bid, state.turn_number)


# ── UI data ───────────────────────────────────────────────────────────────────

def get_listings_display(state: "GameState") -> list[dict]:
    """Return a display-ready list of active auction listings."""
    if not state.world_db:
        return []

    listings = state.world_db.get_active_listings()
    result = []
    for lst in listings:
        turns_left = max(0, lst["expires_turn"] - state.turn_number)
        item_label = (
            "LIFE TOKEN"
            if lst["item_type"] == "life_token"
            else (lst["item_id"] or "Unknown").replace("_", " ").title()
        )
        result.append({
            "listing_id": lst["listing_id"],
            "item":        item_label,
            "item_type":   lst["item_type"],
            "current_bid": lst["current_bid"],
            "turns_left":  turns_left,
            "guild":       lst["auction_guild"],
        })
    return result
