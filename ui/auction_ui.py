"""
Interactive auction-house UI.

Extracted from ``core/game_engine.py``. Pure presentation + user-input
loop on top of ``systems.auction_system``. Owns no state; reads from
``engine.state`` and pushes mutations back through ``auction_system``.

Three actions exposed each iteration:
  - Buy life token (direct)
  - Bid on listing
  - Leave auction house

The "← Back" sub-choice in the bid flow keeps the user in the loop
rather than dropping them out of the auction house.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import questionary

from core.event_bus import bus
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine


def show_auction_house(engine: "GameEngine") -> None:
    """Run the auction house loop until the player leaves."""
    from systems import auction_system

    auction_system.seed_initial_listings(engine.state, engine.guild_registry)

    while True:
        listings = auction_system.get_listings_display(engine.state)
        _render_header()
        _render_listings(listings)
        _render_status(engine, auction_system)

        action = questionary.select(
            "Auction house:",
            choices=["Buy life token (direct)", "Bid on listing", "Leave auction house"],
        ).ask()

        if action is None or action == "Leave auction house":
            break
        if action == "Buy life token (direct)":
            _buy_life_token(engine, auction_system)
        elif action == "Bid on listing" and listings:
            _bid_on_listing(engine, auction_system, listings)


def _render_header() -> None:
    renderer.clear()
    renderer.print_title()
    renderer.console.print("\n  [system_msg][ AUCTION HOUSE ][/system_msg]\n")


def _render_listings(listings: list[dict]) -> None:
    if not listings:
        renderer.console.print("  [dim_text]No active listings.[/dim_text]\n")
        return
    renderer.console.print(f"  {'Item':<25} {'Bid':>8} {'Turns':>6}  {'Guild'}")
    renderer.console.print("  " + "-" * 55)
    for lst in listings:
        renderer.console.print(
            f"  [{lst['listing_id'][:6]}] {lst['item']:<20} {lst['current_bid']:>8,} Shards  "
            f"{lst['turns_left']:>4} turns  {lst['guild']}"
        )


def _render_status(engine: "GameEngine", auction_system) -> None:
    price = auction_system.get_life_token_price(engine.state)
    renderer.console.print(f"\n  Life token market price: [gold]{price:,} Shards[/gold]")
    renderer.console.print(f"  Your Shards: [gold]{engine.state.player.gold:,}[/gold]")


def _buy_life_token(engine: "GameEngine", auction_system) -> None:
    ok, msg = auction_system.buy_life_token_direct(engine.state)
    renderer.print_system_message(msg, style="system_msg" if ok else "system_warning")
    bus.flush()
    renderer.prompt_any_key()


def _bid_on_listing(engine: "GameEngine", auction_system, listings: list[dict]) -> None:
    listing_ids = [
        f"{l['listing_id'][:6]} — {l['item']} (current: {l['current_bid']:,})"
        for l in listings
    ]
    listing_ids.append("← Back")
    selected = questionary.select("Select listing:", choices=listing_ids).ask()
    if not selected or selected == "← Back":
        return

    idx = listing_ids.index(selected)
    chosen = listings[idx]
    min_bid = chosen["current_bid"] + 1
    bid_str = questionary.text(f"Enter bid amount (min {min_bid:,}):").ask()
    try:
        bid_amount = int(bid_str or "0")
    except ValueError:
        renderer.print_error("Invalid bid amount.")
        renderer.prompt_any_key()
        return

    ok, msg = auction_system.place_player_bid(
        chosen["listing_id"], bid_amount, engine.state,
    )
    renderer.print_system_message(msg, style="system_msg" if ok else "system_warning")
    bus.flush()
    renderer.prompt_any_key()
