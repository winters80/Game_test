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
        have = _item_count(player, ing["item_id"])
        if have < ing["qty"]:
            needed = item_registry.get(ing["item_id"])
            name = needed.name if needed else ing["item_id"]
            return False, f"Missing ingredient: {name} x{ing['qty']} (have {have})."

    # All checks passed — consume ingredients
    for ing in recipe.get("ingredients", []):
        player.remove_item(ing["item_id"], ing["qty"])

    output_id = recipe["output_item_id"]
    output_qty = recipe.get("output_qty", 1)
    player.add_item(output_id, output_qty)

    item = item_registry.get(output_id)
    name = item.name if item else output_id
    return True, f"Crafted {name} x{output_qty}."


def _item_count(player: "Player", item_id: str) -> int:
    return sum(slot.quantity for slot in player.inventory if slot.item_id == item_id)


def register_recipe(
    recipes: list[dict],
    recipe: dict,
    item_registry: "ItemRegistry",
) -> tuple[bool, str]:
    """Validate a recipe and add it to the live ``recipes`` list.

    Used for recipes created at runtime (AI world expansion). Every
    ingredient and the output must already exist in the item registry, and
    the recipe_id must be new, so a generated recipe can't replace an
    authored one. Returns (added, reason).
    """
    recipe_id = str(recipe.get("recipe_id", "")).strip()
    if not recipe_id:
        return False, "missing recipe_id"
    if any(r.get("recipe_id") == recipe_id for r in recipes):
        return False, f"recipe_id '{recipe_id}' already exists"
    ingredients = recipe.get("ingredients") or []
    if not ingredients:
        return False, "no ingredients"
    for ing in ingredients:
        if item_registry.get(ing.get("item_id", "")) is None:
            return False, f"unknown ingredient '{ing.get('item_id')}'"
        if int(ing.get("qty", 0)) < 1:
            return False, "ingredient qty must be >= 1"
    output = recipe.get("output_item_id", "")
    if item_registry.get(output) is None:
        return False, f"unknown output '{output}'"
    clean = {
        "recipe_id": recipe_id,
        "name": str(recipe.get("name") or recipe_id.replace("_", " ").title()),
        "ingredients": [{"item_id": i["item_id"], "qty": int(i["qty"])} for i in ingredients],
        "output_item_id": output,
        "output_qty": max(1, int(recipe.get("output_qty", 1))),
        "required_skill": recipe.get("required_skill") or None,
    }
    recipes.append(clean)
    return True, "added"
