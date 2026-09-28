"""
Trader NPCs: buying from and selling to them, and which ones are around.

A trader is any NPC template with a ``trades`` block (entities/npc.NPCTrades).
Hand-authored traders live in data/npcs/*.json; AI world expansion registers
more at runtime. All prices are copper (player.gold is copper); items carry
``value_gold`` in whole gold.

Pure logic: no rendering, no I/O. core/dialogue_handler owns the menus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import COPPER_PER_GOLD
from core.event_bus import Event, bus

if TYPE_CHECKING:
    from core.state_manager import GameState
    from entities.item import Item, ItemRegistry
    from entities.npc import NPCRegistry, NPCTemplate
    from entities.player import Player


@dataclass
class SellableItem:
    item: "Item"
    quantity: int      # how many the player may sell (keeps one if equipped)
    unit_price: int    # copper the trader pays per unit


@dataclass
class WareOffer:
    item: "Item"
    price: int         # copper


# ── Pricing ───────────────────────────────────────────────────────────────────

def trader_buys(trader: "NPCTemplate", item: "Item") -> bool:
    """True if this trader is interested in buying ``item``."""
    if trader.trades is None:
        return False
    for entry in trader.trades.buys:
        if entry.startswith("type:"):
            if item.item_type.value == entry[5:].upper():
                return True
        elif entry == item.item_id:
            return True
    return False


def sell_price(trader: "NPCTemplate", item: "Item") -> int:
    """Copper the trader pays for one ``item``; 0 means worthless to them."""
    if trader.trades is None or item.value_gold <= 0:
        return 0
    return max(1, int(item.value_gold * COPPER_PER_GOLD * trader.trades.buy_rate))


# ── Listing ───────────────────────────────────────────────────────────────────

def _equipped_ids(player: "Player") -> set[str]:
    eq = player.equipped
    return {i for i in (eq.weapon, eq.armor, eq.accessory) if i}


def sellable_items(
    player: "Player", trader: "NPCTemplate", item_registry: "ItemRegistry",
) -> list[SellableItem]:
    """Inventory entries this trader will buy, with per-unit prices."""
    equipped = _equipped_ids(player)
    out: list[SellableItem] = []
    for slot in player.inventory:
        item = item_registry.get(slot.item_id)
        if item is None or not trader_buys(trader, item):
            continue
        qty = slot.quantity - (1 if slot.item_id in equipped else 0)
        price = sell_price(trader, item)
        if qty > 0 and price > 0:
            out.append(SellableItem(item=item, quantity=qty, unit_price=price))
    return out


def wares(trader: "NPCTemplate", item_registry: "ItemRegistry") -> list[WareOffer]:
    """What the trader sells, skipping offers for items that don't exist."""
    if trader.trades is None:
        return []
    out = []
    for offer in trader.trades.sells:
        item = item_registry.get(offer.item_id)
        if item is not None:
            out.append(WareOffer(item=item, price=offer.price))
    return out


# ── Transactions ──────────────────────────────────────────────────────────────

def sell_item(
    player: "Player",
    trader: "NPCTemplate",
    item_id: str,
    item_registry: "ItemRegistry",
    quantity: int = 1,
) -> tuple[bool, str, int]:
    """Sell ``quantity`` of ``item_id`` to the trader. Returns (ok, message, copper earned)."""
    from config import format_currency

    match = next(
        (s for s in sellable_items(player, trader, item_registry) if s.item.item_id == item_id),
        None,
    )
    if match is None:
        return False, f"{trader.name} isn't interested in that.", 0
    quantity = max(1, min(quantity, match.quantity))
    if not player.remove_item(item_id, quantity):
        return False, "You don't have that many.", 0
    earned = match.unit_price * quantity
    player.gold += earned
    return True, f"Sold {quantity}× {match.item.name} for {format_currency(earned)}.", earned


def buy_item(
    player: "Player",
    trader: "NPCTemplate",
    item_id: str,
    item_registry: "ItemRegistry",
) -> tuple[bool, str]:
    """Buy one ``item_id`` from the trader's wares. Returns (ok, message)."""
    from config import format_currency

    offer = next((w for w in wares(trader, item_registry) if w.item.item_id == item_id), None)
    if offer is None:
        return False, f"{trader.name} doesn't sell that."
    if player.gold < offer.price:
        return False, (f"Not enough gold. Need {format_currency(offer.price)}, "
                       f"you have {format_currency(player.gold)}.")
    player.gold -= offer.price
    player.add_item(item_id)
    bus.publish(Event("ITEM_FOUND", {
        "item_id": item_id, "item_name": offer.item.name, "rarity": offer.item.rarity.value,
    }))
    return True, f"Bought {offer.item.name} for {format_currency(offer.price)}."


# ── Presence ──────────────────────────────────────────────────────────────────

def ambient_npcs_here(
    npc_registry: "NPCRegistry", state: "GameState",
) -> list["NPCTemplate"]:
    """Ambient NPCs currently in the player's zone and visible to them.

    Used to add automatic "Talk to …" options, so a trader (hand-authored or
    AI-generated) can appear in a zone without editing that scene's JSON.
    """
    from systems.npc_system import check_npc_visible, get_npc_zone

    zone = state.current_scene_id
    turn = state.player.turn_count
    return [
        npc for npc in npc_registry.all()
        if npc.ambient
        and get_npc_zone(npc, turn) == zone
        and check_npc_visible(npc, state)
    ]
