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


def get_item_display_name(player: "Player", item: "Item", item_registry: "ItemRegistry") -> str:
    """
    Three-tier item identification system:
    - No Inspect skill, never used → unidentified_name (e.g. "Red Vial")
    - Has Inspect OR used once → item.name (e.g. "Health Potion")
    - Used at least once with effect memorized → "Health Potion (+30 HP)"
    """
    from entities.enums import ItemType

    # Non-consumables are always fully identified
    if item.item_type != ItemType.CONSUMABLE or not item.unidentified_name:
        return item.name

    has_inspect = "inspect" in player.skills
    known_value = player.identified_items.get(item.item_id)

    if known_value is not None:
        # Full identification from use history
        if item.effect_type == "heal_hp":
            return f"{item.name} (+{known_value} HP)"
        elif item.effect_type == "heal_mp":
            return f"{item.name} (+{known_value} MP)"
        return item.name

    if has_inspect:
        return item.name

    # Unidentified
    return item.unidentified_name


def get_item_identify_hint(item: "Item") -> str:
    """Vague description shown when player tries to inspect without Inspect skill."""
    if not item.unidentified_name:
        return item.description
    if "Red Vial" in item.unidentified_name or item.effect_type == "heal_hp":
        return "A small vial of red liquid. Smells medicinal — iron and something sharp. Could restore vitality."
    if "Blue Vial" in item.unidentified_name or item.effect_type == "heal_mp":
        return "A small vial of blue liquid. It hums faintly. Could restore arcane reserves."
    return f"An unidentified {item.item_type.value.lower()}. Its properties are unclear."


def use_item(player: "Player", item_id: str, item_registry: "ItemRegistry") -> tuple[bool, str]:
    """
    Consume one of item_id. Applies effect. Returns (success, message).
    Also stores the known effect in player.identified_items.
    """
    from entities.enums import ItemType

    item = item_registry.get(item_id)
    if not item:
        return False, "That item cannot be found."
    if item.item_type != ItemType.CONSUMABLE:
        return False, f"You cannot consume {item.name}."
    if not player.has_item(item_id):
        return False, "You don't have that item."
    if not item.effect_type:
        return False, f"You use {item.name}, but nothing happens."

    restored = 0
    msg = ""

    if item.effect_type == "heal_hp":
        before = player.current_hp
        player.current_hp = min(player.max_hp, player.current_hp + item.effect_value)
        restored = player.current_hp - before
        msg = f"You drink the {item.name}. Restored {restored} HP. ({player.current_hp}/{player.max_hp})"

    elif item.effect_type == "heal_mp":
        before = player.current_mp
        player.current_mp = min(player.max_mp, player.current_mp + item.effect_value)
        restored = player.current_mp - before
        msg = f"You drink the {item.name}. Restored {restored} MP. ({player.current_mp}/{player.max_mp})"

    elif item.effect_type.startswith("buff_"):
        stat = item.effect_type[5:].upper()  # e.g. "buff_STR" → "STR"
        from systems.buff_system import apply_buff
        duration = item.effect_duration if item.effect_duration > 0 else 10
        if stat == "ALL":
            for s in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
                apply_buff(player, s, item.effect_value, duration, item.item_id)
            msg = f"You eat the {item.name}. All stats +{item.effect_value} for {duration} turns."
        else:
            apply_buff(player, stat, item.effect_value, duration, item.item_id)
            msg = f"You eat the {item.name}. {stat} +{item.effect_value} for {duration} turns."

    elif item.effect_type == "rest_meal":
        # Restore HP + apply all-stat buff
        heal = min(item.effect_value, player.max_hp - player.current_hp)
        player.current_hp += heal
        from systems.buff_system import apply_buff
        duration = item.effect_duration if item.effect_duration > 0 else 20
        for s in ("STR", "INT", "AGI", "VIT", "END"):
            apply_buff(player, s, 1, duration, item.item_id)
        msg = f"You eat the {item.name}. Restored {heal} HP. All combat stats +1 for {duration} turns."

    else:
        msg = f"You use {item.name}."

    player.remove_item(item_id, 1)
    player.identified_items[item_id] = item.effect_value  # memorize true effect value
    return True, msg
