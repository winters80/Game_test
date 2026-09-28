"""
Trading between the player and bot adventurers.

Unlike NPC traders (systems/trade_system), a bot has a real purse and a real
bag: it can only sell what it's carrying and only buy what it can afford.

- Bots **sell** spare loot at value × BOT_SELL_MARKUP (they keep their own
  potions and, for traders, nothing is held back from the player).
- Bots **buy** materials and consumables (traders: anything but key items /
  life tokens) at value × BOT_BUY_RATE — better than NPC traders pay, which
  is the reason to find a bot. Never the last copy of an equipped item.

Prices are copper, like player.gold. Pure logic: no rendering, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import COPPER_PER_GOLD

if TYPE_CHECKING:
    from entities.item import Item, ItemRegistry
    from entities.player import Player
    from systems.bot_system import BotAgent

BOT_SELL_MARKUP = 1.25
BOT_BUY_RATE = 0.6
_BOT_BUYS_TYPES = {"MATERIAL", "CONSUMABLE"}
_NEVER_TRADED = {"KEY_ITEM", "LIFE_TOKEN"}
_BOT_KEEPS = {"health_potion": 1, "health_potion_major": 1}


@dataclass
class BotOffer:
    item: "Item"
    quantity: int
    price: int          # copper per unit


def _value(item: "Item") -> int:
    return max(0, item.value_gold) * COPPER_PER_GOLD


def bot_sells(bot: "BotAgent", item_registry: "ItemRegistry") -> list[BotOffer]:
    """What the bot will sell to the player, and at what price."""
    out = []
    for item_id, qty in sorted(bot.inventory.items()):
        item = item_registry.get(item_id)
        if item is None or item.item_type.value in _NEVER_TRADED or _value(item) <= 0:
            continue
        spare = qty - _BOT_KEEPS.get(item_id, 0)
        if spare > 0:
            out.append(BotOffer(item, spare, max(1, int(_value(item) * BOT_SELL_MARKUP))))
    return out


def bot_buys(bot: "BotAgent", item: "Item") -> bool:
    if item.item_type.value in _NEVER_TRADED or _value(item) <= 0:
        return False
    return bot.archetype == "trader" or item.item_type.value in _BOT_BUYS_TYPES


def bot_buy_price(item: "Item") -> int:
    return max(1, int(_value(item) * BOT_BUY_RATE))


def player_sellables(player: "Player", bot: "BotAgent", item_registry: "ItemRegistry") -> list[BotOffer]:
    """Player inventory the bot would buy (quantity capped by what it can afford)."""
    eq = player.equipped
    equipped = {i for i in (eq.weapon, eq.armor, eq.accessory) if i}
    out = []
    for slot in player.inventory:
        item = item_registry.get(slot.item_id)
        if item is None or not bot_buys(bot, item):
            continue
        qty = slot.quantity - (1 if slot.item_id in equipped else 0)
        price = bot_buy_price(item)
        qty = min(qty, bot.gold // price)
        if qty > 0:
            out.append(BotOffer(item, qty, price))
    return out


def buy_from_bot(
    player: "Player", bot: "BotAgent", item_id: str, item_registry: "ItemRegistry",
) -> tuple[bool, str]:
    from config import format_currency

    offer = next((o for o in bot_sells(bot, item_registry) if o.item.item_id == item_id), None)
    if offer is None:
        return False, f"{bot.name} isn't selling that."
    if player.gold < offer.price:
        return False, (f"Not enough gold. Need {format_currency(offer.price)}, "
                       f"you have {format_currency(player.gold)}.")
    player.gold -= offer.price
    bot.gold += offer.price
    bot.remove_item(item_id)
    player.add_item(item_id)
    bot.remember(f"Sold {offer.item.name} to {player.name}")
    return True, f"Bought {offer.item.name} from {bot.name} for {format_currency(offer.price)}."


def sell_to_bot(
    player: "Player", bot: "BotAgent", item_id: str, item_registry: "ItemRegistry", quantity: int = 1,
) -> tuple[bool, str, int]:
    from config import format_currency

    match = next((o for o in player_sellables(player, bot, item_registry) if o.item.item_id == item_id), None)
    if match is None:
        return False, f"{bot.name} can't or won't buy that.", 0
    quantity = max(1, min(quantity, match.quantity))
    if not player.remove_item(item_id, quantity):
        return False, "You don't have that many.", 0
    total = match.price * quantity
    player.gold += total
    bot.gold -= total
    bot.add_item(item_id, quantity)
    bot.remember(f"Bought {quantity}× {match.item.name} from {player.name}")
    return True, f"Sold {quantity}× {match.item.name} to {bot.name} for {format_currency(total)}.", total
