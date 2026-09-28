"""
Engine side of world growth (logic lives in systems/world_growth.py).

- ``on_world_action``: the player picked an AI option tagged
  ``world_action:VERB:SUBJECT``. First sighting → queue a background
  expansion and log a discovery. Already expanded → gather the yield item.
- ``apply_expansion_result``: a background ``world_expansion`` result
  arrived. Build the safe bundle, register it, store it, reward the player
  and announce what changed.
- ``reload_world_growth``: on load/new game, clear any other save's
  generated content and re-register this save's bundles.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import WORLD_YIELD_COOLDOWN_TURNS, feature
from core.event_bus import Event, bus
from systems import world_growth as wg
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)

_KNOWN_ITEM_TYPES = {"MATERIAL", "CONSUMABLE"}
_PLACE_NAMES = {
    "village_start": "in the outer market",
    "verath_city": "in Verath",
    "camp_rest": "at the roadside camp",
}


def _db(engine: "GameEngine"):
    if not feature("world_db") or engine.state is None:
        return None
    return engine.state.world_db


def on_world_action(engine: "GameEngine", verb: str, subject: str, narrative: str = "") -> None:
    """Record a player action and let the world react to it."""
    db = _db(engine)
    if db is None:
        return
    key = wg.action_key(verb, subject)
    norm_subject = key.split(":", 1)[1]
    player = engine.state.player
    turn = player.turn_count
    row = db.get_world_action(key)

    if row is None:
        db.record_world_action(key, verb, norm_subject, engine.state.current_scene_id, turn)
        if _submit_expansion(engine, key, verb, norm_subject, narrative):
            renderer.print_system_message(
                f"✦ DISCOVERY — The System logs a new activity: "
                f"{verb} {norm_subject.replace('_', ' ')}. The world may answer.",
                style="system_msg",
            )
        else:
            db.set_world_action_status(key, "no_ai")
        return

    row = db.record_world_action(key, verb, norm_subject, engine.state.current_scene_id, turn)
    expansion = db.get_world_expansion(key)
    yield_id = (expansion or {}).get("yield_item_id") or ""
    item = engine.item_registry.get(yield_id) if yield_id else None
    if item is None:
        return
    if wg.yield_ready(row, turn, WORLD_YIELD_COOLDOWN_TURNS):
        player.add_item(item.item_id)
        db.set_world_action_yield_turn(key, turn)
        bus.publish(Event("ITEM_FOUND", {
            "item_id": item.item_id, "item_name": item.name, "rarity": item.rarity.value,
        }))
        bus.flush()
        renderer.print_success(f"You gather: {item.name}.")
    else:
        renderer.print_system_message(
            f"You've taken what {item.name.lower()} there was here for now.", style="dim_text",
        )


def _submit_expansion(engine: "GameEngine", key: str, verb: str, subject: str, narrative: str) -> bool:
    ai = getattr(engine, "ai_service", None)
    if ai is None or not ai.is_available or not ai.has_background:
        return False
    scene = engine.scene_registry.get(engine.state.current_scene_id)
    scene_title = scene.title if scene else engine.state.current_scene_id
    known = [
        i.item_id for i in engine.item_registry.all()
        if i.item_type.value in _KNOWN_ITEM_TYPES
    ]
    return ai.submit_world_expansion_async(
        action_key=key, verb=verb, subject=subject,
        zone_id=engine.state.current_scene_id, zone_name=scene_title,
        scene_title=scene_title, narrative=narrative,
        player=engine.state.player, known_items=known,
    )


def apply_expansion_result(engine: "GameEngine", result: dict) -> None:
    """Integrate a background ``world_expansion`` result into the live world."""
    db = _db(engine)
    if db is None:
        return
    key = result.get("action_key", "")
    if not key or db.get_world_expansion(key) is not None:
        return
    player = engine.state.player
    bundle = wg.build_bundle(
        result["response"], result.get("skill"), key, result.get("zone_id", ""),
        player.level, engine.item_registry, engine.npc_registry,
        engine.skill_registry, engine.recipes,
    )
    if wg.bundle_is_empty(bundle):
        db.set_world_action_status(key, "empty")
        return
    wg.register_bundle(bundle, engine.item_registry, engine.npc_registry,
                       engine.skill_registry, engine.recipes)
    db.store_world_expansion(key, bundle, bundle["yield_item_id"], player.turn_count)
    # The discovery reward below counts as this action's first yield.
    db.set_world_action_yield_turn(key, player.turn_count)
    _announce_and_reward(engine, bundle)


def _announce_and_reward(engine: "GameEngine", bundle: dict) -> None:
    player = engine.state.player
    renderer.console.print()
    reward_id = bundle.get("yield_item_id") or (bundle["items"][0]["item_id"] if bundle.get("items") else "")
    reward = engine.item_registry.get(reward_id) if reward_id else None
    if reward is not None:
        player.add_item(reward.item_id)
        bus.publish(Event("ITEM_FOUND", {
            "item_id": reward.item_id, "item_name": reward.name, "rarity": reward.rarity.value,
        }))
        renderer.console.print(
            f"  [system_msg]✦ NEW ITEM DISCOVERED[/system_msg]  [scene_text]{reward.name}[/scene_text]"
            f" [dim_text]— added to your pack[/dim_text]"
        )
    if bundle.get("skill"):
        from systems.skill_system import learn_skill
        ok, _ = learn_skill(player, bundle["skill"]["skill_id"], engine.skill_registry)
        if ok:
            renderer.console.print(
                f"  [system_msg]✦ NEW SKILL[/system_msg]  [scene_text]{bundle['skill']['name']}[/scene_text]"
            )
    for recipe in bundle.get("recipes", []):
        renderer.console.print(
            f"  [system_msg]✦ NEW RECIPE[/system_msg]  [scene_text]{recipe['name']}[/scene_text]"
            f" [dim_text]— see [C] Craft[/dim_text]"
        )
    npc = bundle.get("npc")
    if npc:
        where = "on the road between the market, the camps and Verath" if npc.get("route") \
            else _PLACE_NAMES.get(npc["zone_id"], npc["zone_id"].replace("_", " "))
        renderer.console.print(
            f"  [system_msg]✦ WORD SPREADS[/system_msg]  [scene_text]{npc['name']} now trades "
            f"{where}.[/scene_text]"
        )
    summary = bundle.get("summary") or ""
    if summary:
        renderer.console.print(f"  [dim_text]{summary}[/dim_text]")
    bus.flush()
    try:
        engine.state.world_db.store_world_event(
            "world_growth", summary or "The world shifted in response to you.",
            zone_id=bundle.get("zone_id") or None,
            title="World growth", generated_turn=player.turn_count,
        )
    except Exception:
        logger.info("Could not log world growth event", exc_info=True)
    engine.state.mark_dirty()


def reload_world_growth(engine: "GameEngine") -> None:
    """Clear other saves' generated content; register this save's bundles."""
    wg.clear_generated(engine.item_registry, engine.npc_registry,
                       engine.skill_registry, engine.recipes)
    db = _db(engine)
    if db is None:
        return
    for row in db.load_world_expansions():
        try:
            wg.register_bundle(row["bundle"], engine.item_registry, engine.npc_registry,
                               engine.skill_registry, engine.recipes)
        except Exception:
            logger.warning("Could not restore world growth bundle %s",
                           row.get("action_key"), exc_info=True)
