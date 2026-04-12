"""
Alchemy / crafting system.

Recipes are defined in data/crafting/recipes.json.
Each recipe lists ingredients (item_id + qty), an output item, and an optional required skill.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entities.player import Player
    from entities.item import ItemRegistry


def load_recipes(data_dir: Path) -> list[dict]:
    """Load all crafting recipes from data/crafting/recipes.json."""
    path = data_dir / "crafting" / "recipes.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def craft_item(
    player: "Player",
    recipe_id: str,
    item_registry: "ItemRegistry",
    recipes: list[dict],
) -> tuple[bool, str]:
    """Attempt to craft the item defined by recipe_id.

    Returns (success, message).
    On success the output item is added to the player's inventory and
    ingredients are consumed.
    """
    recipe = next((r for r in recipes if r["recipe_id"] == recipe_id), None)
    if recipe is None:
        return False, "Unknown recipe."

    required_skill = recipe.get("required_skill")
    if required_skill and required_skill not in player.skills:
        return False, f"Requires skill: {required_skill}."

    for ing in recipe.get("ingredients", []):
        if not player.has_item(ing["item_id"]):
            needed = item_registry.get(ing["item_id"])
            name = needed.name if needed else ing["item_id"]
            return False, f"Missing ingredient: {name} x{ing['qty']}."

    # All checks passed — consume ingredients
    for ing in recipe.get("ingredients", []):
        player.remove_item(ing["item_id"], ing["qty"])

    output_id = recipe["output_item_id"]
    output_qty = recipe.get("output_qty", 1)
    player.add_item(output_id, output_qty)

    item = item_registry.get(output_id)
    name = item.name if item else output_id
    return True, f"Crafted {name} x{output_qty}."
