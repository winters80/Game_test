"""
World growth: the world reacts to what the player does.

Flow (see CLAUDE.md "World growth"):
  1. An AI "[?]" option carries ``world_action:VERB:SUBJECT``. When the
     player picks it, the engine records the action (world_db.world_actions).
  2. The first time an action is seen, the primary model is asked, in the
     background, how the world should grow: new goods, recipes, a trader,
     maybe a skill (``AIWorldExpansionResponse``).
  3. ``build_bundle`` turns that untrusted response into a safe bundle:
     every id is remapped under ``gen_``, values are clamped by
     systems/economy, references are checked, and junk is dropped.
  4. ``register_bundle`` puts the bundle into the live registries. The same
     bundle is stored per save and re-registered on load.
  5. Repeating an expanded action yields its ``yield_item`` (on cooldown).

Pure logic: registries are mutated, but no I/O, rendering or AI calls.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from systems.ai_trigger_policy import slugify

if TYPE_CHECKING:
    from entities.item import ItemRegistry
    from entities.npc import NPCRegistry
    from entities.skill import Skill, SkillRegistry

logger = logging.getLogger(__name__)

GEN_PREFIX = "gen_"

# Where a generated trader may set up. "road" = travelling between them.
LOCATION_ZONES = {
    "outer_market": "village_start",
    "verath": "verath_city",
    "camp": "camp_rest",
}
ROAD_ROUTE = ["village_start", "camp_rest", "verath_city"]

# Only these existing item types may be referenced in generated trades.
_TRADEABLE_TYPES = {"MATERIAL", "CONSUMABLE"}
_TRADER_BUY_RATE = 0.5
_TRADER_MAX_MARKUP = 3          # sell price between value and value × this

_STOPWORDS = {
    "the", "a", "an", "some", "for", "to", "at", "into", "in", "on", "with",
    "your", "my", "his", "her", "their", "its", "of", "from", "and", "up",
    "try", "attempt", "carefully", "quickly", "quietly",
}


# ── Action keys ───────────────────────────────────────────────────────────────

def normalize_subject(subject: str) -> str:
    """Collapse surface variation so "gold veins" and "gold" share a key.

    Drops filler words ("vein", "deposit", "chunk"…), strips simple plurals.
    """
    filler = {"vein", "veins", "deposit", "deposits", "seam", "seams", "chunk",
              "chunks", "piece", "pieces", "bit", "bits", "some", "patch", "patches"}
    words = [w for w in slugify(subject).split("_") if w and w not in filler and w not in _STOPWORDS]
    out = []
    for w in words:
        if len(w) > 3 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return "_".join(out[:3]) or slugify(subject)


def action_key(verb: str, subject: str) -> str:
    return f"{slugify(verb, 20)}:{normalize_subject(subject)}"


def derive_world_action(label: str) -> str | None:
    """Fallback tag from an option label when the model forgot one.

    "Mine the gold vein" → "world_action:mine:gold_vein". None if the label
    has no usable verb + subject.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", label.lower()) if w]
    words = [w for w in words if w not in {"ai"}]
    if len(words) < 2:
        return None
    verb = words[0]
    subject = [w for w in words[1:] if w not in _STOPWORDS][:3]
    if not subject:
        return None
    return f"world_action:{verb}:{'_'.join(subject)}"


# ── Bundle building ───────────────────────────────────────────────────────────

def _fresh_id(base: str, taken) -> str:
    base = slugify(base) or "thing"
    cand = base if base.startswith(GEN_PREFIX) else f"{GEN_PREFIX}{base}"
    cand = cand[:40]
    n = 2
    while taken(cand):
        cand = f"{cand[:36]}_{n}"
        n += 1
    return cand


def build_bundle(
    response: Any,                       # ai.response_validator.AIWorldExpansionResponse
    skill: "Skill | None",
    action_key_: str,
    action_zone: str,
    player_level: int,
    item_registry: "ItemRegistry",
    npc_registry: "NPCRegistry | None",
    skill_registry: "SkillRegistry | None",
    recipes: list[dict],
) -> dict:
    """Turn an AI expansion into a safe, JSON-serialisable bundle.

    Nothing is registered here. Unusable pieces are dropped, not repaired.
    """
    from entities.item import Item
    from systems.economy import generated_effect_cap, generated_item_value_cap

    new_items: dict[str, dict] = {}          # new id → Item dict
    id_map: dict[str, str] = {}              # AI id → final id (new or existing)

    def taken_item(i: str) -> bool:
        return item_registry.get(i) is not None or i in new_items

    def resolve(ref: str) -> str | None:
        ref = slugify(ref)
        if ref in id_map:
            return id_map[ref]
        existing = item_registry.get(ref)
        if existing is not None and existing.item_type.value in _TRADEABLE_TYPES:
            return ref
        return None

    specs = ([response.yield_item] if response.yield_item else []) + list(response.items)
    for spec in specs:
        if not spec.item_id or not spec.name.strip():
            continue
        if spec.item_id in id_map:
            continue
        existing = item_registry.get(spec.item_id)
        if existing is not None and not spec.item_id.startswith(GEN_PREFIX):
            # The model named an existing item: reference it, don't redefine it.
            if existing.item_type.value in _TRADEABLE_TYPES:
                id_map[spec.item_id] = spec.item_id
            continue
        new_id = _fresh_id(spec.item_id, taken_item)
        cap = generated_item_value_cap(player_level, spec.item_type)
        is_consumable = spec.item_type == "CONSUMABLE"
        effect_type = spec.effect_type if is_consumable else ""
        effect_value = min(spec.effect_value, generated_effect_cap(player_level)) if effect_type else 0
        item = Item.model_validate({
            "item_id": new_id,
            "name": spec.name.strip()[:40],
            "rarity": "COMMON",
            "item_type": spec.item_type,
            "description": (spec.description or spec.name).strip()[:200],
            "value_gold": max(1, min(spec.value_gold, cap)),
            "stackable": True,
            "effect_type": effect_type,
            "effect_value": effect_value,
        })
        new_items[new_id] = item.model_dump(mode="json")
        id_map[spec.item_id] = new_id

    yield_item_id = ""
    if response.yield_item and response.yield_item.item_id in id_map:
        yield_item_id = id_map[response.yield_item.item_id]

    # Recipes: every reference must resolve; ids never collide with authored ones.
    taken_recipes = {r.get("recipe_id") for r in recipes}
    bundle_recipes: list[dict] = []
    for r in response.recipes:
        ingredients = []
        for ing in r.ingredients:
            rid = resolve(ing.item_id)
            if rid is None:
                ingredients = []
                break
            ingredients.append({"item_id": rid, "qty": ing.qty})
        output = resolve(r.output_item_id)
        if not ingredients or output is None:
            continue
        recipe_id = _fresh_id(r.recipe_id or f"{output}_recipe",
                              lambda i: i in taken_recipes)
        taken_recipes.add(recipe_id)
        out_name = (new_items.get(output) or {}).get("name") or output.replace("_", " ").title()
        bundle_recipes.append({
            "recipe_id": recipe_id,
            "name": (r.name or f"{out_name}").strip()[:40],
            "ingredients": ingredients,
            "output_item_id": output,
            "output_qty": max(1, min(r.output_qty, 5)),
            "required_skill": None,
        })

    # Trader: ambient, sells at or above value (no buy-low/sell-high loops).
    npc_dict = None
    t = response.trader
    if t is not None and t.name.strip() and npc_registry is not None:
        buys: list[str] = []
        for b in t.buys:
            tag = b.strip()
            if tag.lower().startswith("type:") and tag[5:].upper() in _TRADEABLE_TYPES:
                buys.append(f"type:{tag[5:].upper()}")
            else:
                rid = resolve(tag)
                if rid:
                    buys.append(rid)
        sells = []
        for offer in t.sells:
            rid = resolve(offer.item_id)
            if rid is None:
                continue
            value = (new_items.get(rid) or {}).get("value_gold")
            if value is None:
                value = item_registry.get(rid).value_gold
            value = max(1, int(value))
            price_gold = max(value, min(offer.price_gold, value * _TRADER_MAX_MARKUP))
            sells.append({"item_id": rid, "price": price_gold * 100})
        buys = list(dict.fromkeys(buys))
        if buys or sells:
            template_id = _fresh_id(f"npc_{t.name}", lambda i: npc_registry.get(i) is not None)
            if t.location == "road":
                zone, route = ROAD_ROUTE[0], list(ROAD_ROUTE)
            else:
                zone, route = LOCATION_ZONES.get(t.location, "village_start"), []
            name = t.name.strip()[:30]
            greeting = (t.greeting or f"'{name}. Trading, are we?'").strip()[:300]
            npc_dict = {
                "template_id": template_id,
                "npc_id": template_id,
                "name": name,
                "role": "merchant",
                "zone_id": zone,
                "route": route,
                "description": (t.description or f"{name}, a trader.").strip()[:300],
                "is_essential": True,
                "ambient": True,
                "is_ai_generated": True,
                "trades": {"buys": buys, "sells": sells, "buy_rate": _TRADER_BUY_RATE},
                "disposition_hooks": {"default": "root"},
                "dialogue_nodes": {"root": {
                    "node_id": "root",
                    "npc_text": greeting,
                    "options": [{
                        "option_id": "leave",
                        "label": "Maybe later.",
                        "npc_response": "'I'll be here. Or somewhere near.'",
                        "leads_to_node": "__exit__",
                    }],
                }},
            }

    skill_dict = None
    if skill is not None:
        taken_skill = (lambda i: skill_registry.get(i) is not None) if skill_registry else (lambda i: False)
        data = skill.model_dump(mode="json")
        data["skill_id"] = _fresh_id(skill.skill_id or skill.name, taken_skill)
        data["is_ai_generated"] = True
        skill_dict = data

    return {
        "action_key": action_key_,
        "zone_id": action_zone,
        "summary": (response.summary or "").strip()[:240],
        "yield_item_id": yield_item_id,
        "items": list(new_items.values()),
        "recipes": bundle_recipes,
        "npc": npc_dict,
        "skill": skill_dict,
    }


def bundle_is_empty(bundle: dict) -> bool:
    return not (bundle.get("items") or bundle.get("recipes")
                or bundle.get("npc") or bundle.get("skill"))


# ── Registration ──────────────────────────────────────────────────────────────

def register_bundle(
    bundle: dict,
    item_registry: "ItemRegistry",
    npc_registry: "NPCRegistry | None",
    skill_registry: "SkillRegistry | None",
    recipes: list[dict],
) -> list[str]:
    """Register a bundle's content in the live registries. Returns what was added.

    Idempotent for recipes (duplicates are skipped), so reloading is safe.
    """
    from entities.item import Item
    from entities.npc import NPCTemplate
    from entities.skill import Skill
    from systems.alchemy_system import register_recipe

    added: list[str] = []
    for data in bundle.get("items", []):
        item = Item.model_validate(data)
        item_registry.register(item)
        added.append(f"item:{item.item_id}")
    for recipe in bundle.get("recipes", []):
        ok, reason = register_recipe(recipes, recipe, item_registry)
        if ok:
            added.append(f"recipe:{recipe['recipe_id']}")
        elif "already exists" not in reason:
            logger.info("World growth recipe %s skipped: %s", recipe.get("recipe_id"), reason)
    if bundle.get("npc") and npc_registry is not None:
        npc = NPCTemplate.model_validate(bundle["npc"])
        npc_registry.register(npc)
        added.append(f"npc:{npc.template_id}")
    if bundle.get("skill") and skill_registry is not None:
        skill = Skill.model_validate(bundle["skill"])
        skill_registry.register(skill)
        added.append(f"skill:{skill.skill_id}")
    return added


def clear_generated(
    item_registry: "ItemRegistry",
    npc_registry: "NPCRegistry | None",
    skill_registry: "SkillRegistry | None",
    recipes: list[dict],
) -> None:
    """Remove all world-growth content (``gen_`` ids) from the live registries.

    Called before registering a loaded save's bundles, so one save's
    generated world never leaks into another played in the same session.
    """
    for i in item_registry.ids():
        if i.startswith(GEN_PREFIX):
            item_registry.remove(i)
    if npc_registry is not None:
        for i in npc_registry.ids():
            if i.startswith(GEN_PREFIX):
                npc_registry.remove(i)
    if skill_registry is not None:
        for i in skill_registry.ids():
            if i.startswith(GEN_PREFIX):
                skill_registry.remove(i)
    recipes[:] = [r for r in recipes if not str(r.get("recipe_id", "")).startswith(GEN_PREFIX)]


def yield_ready(action_row: dict | None, turn: int, cooldown: int) -> bool:
    """Has the cooldown on gathering this action's yield passed?"""
    if not action_row:
        return False
    last = int(action_row.get("last_yield_turn", -1))
    return last < 0 or turn - last >= cooldown
