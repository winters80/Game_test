from __future__ import annotations

from typing import TYPE_CHECKING

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.player import Player
    from entities.item import Item, ItemRegistry


def pick_up_item(player: "Player", item_id: str, item_registry: "ItemRegistry", quantity: int = 1) -> bool:
    item = item_registry.get(item_id)
    if not item:
        return False
    player.add_item(item_id, quantity)
    bus.publish(Event("ITEM_FOUND", {
        "item_id": item_id,
        "item_name": item.name,
        "rarity": item.rarity.value,
    }))
    return True


def equip_item(player: "Player", item_id: str, item_registry: "ItemRegistry") -> tuple[bool, str]:
    item = item_registry.get(item_id)
    if not item:
        return False, "Item not found."
    if not player.has_item(item_id):
        return False, "You don't have that item."

    slot_map = {
        "WEAPON": "weapon",
        "ARMOR": "armor",
        "ACCESSORY": "accessory",
    }
    slot = slot_map.get(item.item_type.value)
    if not slot:
        return False, f"Cannot equip {item.item_type.value} items."

    # Unequip existing
    current = getattr(player.equipped, slot)
    if current:
        # Apply reverse of current item stats
        current_item = item_registry.get(current)
        if current_item and current_item.stats_bonus:
            _apply_stats(player, current_item.stats_bonus, reverse=True)

    # Equip new
    setattr(player.equipped, slot, item_id)
    if item.stats_bonus:
        _apply_stats(player, item.stats_bonus)

    return True, f"Equipped {item.name}."


def unequip_item(player: "Player", slot: str, item_registry: "ItemRegistry") -> tuple[bool, str]:
    current = getattr(player.equipped, slot, None)
    if not current:
        return False, "Nothing equipped in that slot."
    item = item_registry.get(current)
    if item and item.stats_bonus:
        _apply_stats(player, item.stats_bonus, reverse=True)
    setattr(player.equipped, slot, None)
    return True, f"Unequipped {item.name if item else current}."


def _apply_stats(player: "Player", stats_bonus: object, reverse: bool = False) -> None:
    multiplier = -1 if reverse else 1
    for stat in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
        val = getattr(stats_bonus, stat, 0)
        if val != 0:
            current = getattr(player.stats, stat)
            setattr(player.stats, stat, current + val * multiplier)


def get_wild_catalysts(player: "Player", item_registry: "ItemRegistry", class_registry: object) -> list[str]:
    """Return item IDs that are combo_catalysts but not referenced in any known combo."""
    known_combo_items: set[str] = set()
    for cls in class_registry.combo_classes():
        if cls.combo_requirements:
            known_combo_items.update(cls.combo_requirements.required_items)

    wild = []
    for slot in player.inventory:
        item = item_registry.get(slot.item_id)
        if item and item.combo_catalyst and slot.item_id not in known_combo_items:
            wild.append(slot.item_id)
    return wild
