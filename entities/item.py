from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from entities.enums import Rarity, ItemType
from entities.player import Stats


class Item(BaseModel):
    item_id: str
    name: str
    rarity: Rarity = Rarity.COMMON
    item_type: ItemType
    description: str
    flavor_text: str = ""
    stats_bonus: Stats | None = None
    effects: list[str] = []              # Skill IDs granted when equipped
    combo_catalyst: bool = False         # Can contribute to AI-class divergence detection
    value_gold: int = 0
    weight: float = 1.0
    stackable: bool = False
    quantity: int = 1
    effect_type:  str = ""          # "heal_hp" | "heal_mp" | "" for non-consumables
    effect_value: int = 0           # amount restored
    effect_duration: int = 0        # turns this buff lasts (for consumable buffs)
    unidentified_name: str = ""     # shown before player has Inspect or has used the item


class ItemRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def load_from_dir(self, items_dir: Path) -> None:
        for path in items_dir.glob("*.json"):
            data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                item = Item.model_validate(entry)
                self._items[item.item_id] = item

    def register(self, item: Item) -> None:
        self._items[item.item_id] = item

    def get(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def all(self) -> list[Item]:
        return list(self._items.values())
