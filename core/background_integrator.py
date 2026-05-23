"""
Drain BackgroundGenerator results and apply them to live game state.

Extracted from ``core/game_engine.py`` to keep the engine's main loop method
focused on player input + scene rendering. Each ``_handle_*`` here owns one
result-type's integration, and the dispatcher is a flat ``if`` ladder so
adding a new background task type is an obvious one-line addition.

The dispatcher takes the engine as a parameter rather than living on it — the
handlers mutate ``engine.state``, ``engine.npc_registry``, etc. directly, so
the engine remains the source-of-truth for live game state.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import feature
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


# ── Bot goal cycling ─────────────────────────────────────────────────────────

_BOT_GOAL_CYCLE: dict[str, str] = {
    "explore": "trade",
    "trade":   "rest",
    "rest":    "explore",
    "combat":  "rest",
    "idle":    "explore",
}


def _advance_bot_goal(bot) -> None:
    """Cycle the bot's current goal and trim memory to last 10 entries."""
    bot.current_goal = _BOT_GOAL_CYCLE.get(bot.current_goal, "explore")
    if len(bot.memory) > 20:
        bot.memory = bot.memory[-10:]


# ── Public entry point ───────────────────────────────────────────────────────

def integrate_results(engine: "GameEngine") -> None:
    """Poll the BG generator and apply every ready result to live game state."""
    if not engine._bg_generator:
        return
    results = engine._bg_generator.poll_results()
    if not results:
        return

    for result in results:
        try:
            _dispatch(engine, result)
        except Exception as exc:
            logger.warning(f"Content integration error: {exc}")


def _dispatch(engine: "GameEngine", result: dict) -> None:
    rtype = result.get("type")
    if rtype == "quest":
        _handle_quest(engine, result)
    elif rtype == "narrative":
        _handle_narrative(engine, result)
    elif rtype == "npc_branch":
        _handle_npc_branch(engine, result)
    elif rtype == "bot_action":
        _handle_bot_action(engine, result)
    elif rtype == "guild_tick_results":
        _handle_guild_tick(result)
    elif rtype == "director_fired":
        # Director analysis already queued its own follow-up tasks; no action.
        logger.debug(
            f"Director analysis integrated: "
            f"{result.get('target_count', 0)} target(s) queued"
        )
    elif rtype in ("world_event", "rumor", "lore_entry", "area_activity"):
        _handle_world_event(engine, result, rtype)


# ── Per-type handlers ────────────────────────────────────────────────────────

def _handle_quest(engine: "GameEngine", result: dict) -> None:
    if not (feature("quest_system") and engine.quest_registry):
        return
    template = result.get("template")
    if not template:
        return
    engine.quest_registry.register(template)
    if feature("world_db") and engine.state.world_db:
        engine.state.world_db.store_bg_content(
            "quest", template.template_id,
            template.model_dump(),
            zone_id=engine.state.current_scene_id,
            generated_turn=engine.state.player.turn_count,
        )
    renderer.console.print(
        f"\n  [system_msg][ NEW QUEST AVAILABLE ][/system_msg]  "
        f"[scene_text]{template.title}[/scene_text]"
    )


def _handle_narrative(engine: "GameEngine", result: dict) -> None:
    zone_id = result.get("zone_id", "")
    text    = result.get("text", "")
    if not (zone_id and text):
        return
    scene = engine.scene_registry.get(zone_id)
    if scene:
        scene.entrance_text = [text]
    if feature("world_db") and engine.state.world_db:
        engine.state.world_db.store_bg_content(
            "narrative", zone_id,
            {"zone_id": zone_id, "text": text},
            zone_id=zone_id,
            generated_turn=engine.state.player.turn_count,
        )


def _handle_npc_branch(engine: "GameEngine", result: dict) -> None:
    if not (feature("npc_system") and engine.npc_registry):
        return
    npc_id = result.get("npc_id", "")
    node   = result.get("node", {})
    if not (npc_id and node):
        return
    npc = engine.npc_registry.get(npc_id)
    if npc and hasattr(npc, "dialogue_nodes") and node.get("node_id"):
        from entities.npc import NPCDialogueNode
        try:
            npc.dialogue_nodes[node["node_id"]] = NPCDialogueNode.model_validate(node)
        except Exception:
            logger.warning(
                "Failed to validate AI-generated NPC dialogue node npc=%s node=%s",
                npc_id, node.get("node_id"), exc_info=True,
            )
    if feature("world_db") and engine.state.world_db:
        engine.state.world_db.store_bg_content(
            "npc_branch", npc_id, node,
            generated_turn=engine.state.player.turn_count,
        )


def _handle_bot_action(engine: "GameEngine", result: dict) -> None:
    """Apply a bot's chosen action and surface it in-world.

    Restructured from the original game_engine version so the per-action
    handlers form one clean if/elif ladder (previously the arrival/departure
    announcement sat between branches, which made the trade/rest/craft
    branches accidentally elif-chained off it).
    """
    if not (feature("bot_system") and engine._bot_manager):
        return
    bot_id = result.get("bot_id", "")
    action = result.get("action", "")
    target = result.get("target", "")
    bot    = engine._bot_manager.get(bot_id)
    if not (bot and action):
        return

    prev_zone   = bot.current_zone_id
    player_zone = engine.state.current_scene_id
    turn        = engine.state.player.turn_count
    event_text: str | None = None

    # ── Per-action handlers (one flat ladder) ────────────────────────────
    if action == "move_zone":
        from systems.world_zones import is_adjacent, get_connected
        if is_adjacent(bot.current_zone_id, target):
            event_text = (
                f"{bot.name} was spotted traveling toward "
                f"{target.replace('_', ' ').title()}."
            )
            bot.current_zone_id = target
        else:
            adj = get_connected(bot.current_zone_id)
            if adj:
                bot.current_zone_id = adj[0]
    elif action == "trade":
        cost = min(50, bot.gold)
        bot.gold -= cost
        bot.memory.append(f"Traded at {target} for {cost}g (turn {turn})")
        event_text = (
            f"{bot.name} completed a trade deal in "
            f"{target.replace('_', ' ').title()}."
        )
    elif action == "rest":
        bot.current_goal = "idle"
    elif action == "craft":
        bot.memory.append(f"Crafted at {target} (turn {turn})")
        event_text = (
            f"{bot.name} was seen working at a crafting bench in "
            f"{bot.current_zone_id.replace('_', ' ').title()}."
        )
    elif action == "talk_npc":
        bot.memory.append(f"Spoke with {target} (turn {turn})")
        event_text = (
            f"{bot.name} was overheard talking to "
            f"{target.replace('_', ' ').title()} in "
            f"{bot.current_zone_id.replace('_', ' ').title()}."
        )

    # ── Zone-boundary announcements ──────────────────────────────────────
    if action == "move_zone" and prev_zone != bot.current_zone_id:
        if bot.current_zone_id == player_zone:
            renderer.console.print(
                f"\n  [dim_text]✦  {bot.name} has arrived in the area.[/dim_text]"
            )
        elif prev_zone == player_zone:
            renderer.console.print(
                f"\n  [dim_text]✦  {bot.name} has left, "
                f"heading to {bot.current_zone_id.replace('_', ' ').title()}.[/dim_text]"
            )

    # ── In-zone activity surfaces immediately ────────────────────────────
    if event_text and bot.current_zone_id == player_zone:
        renderer.console.print(
            f"\n  [dim_text][ HERE ][/dim_text]  "
            f"[italic scene_text]{event_text}[/italic scene_text]"
        )

    # ── Bookkeeping + persist ────────────────────────────────────────────
    if turn - bot.turn_last_acted >= 10:
        _advance_bot_goal(bot)
    bot.turn_last_acted = turn

    if feature("world_db") and engine.state.world_db:
        engine._bot_manager.save_to_db(engine.state.world_db)
        if event_text:
            engine.state.world_db.store_world_event(
                event_type="area_activity",
                event_text=event_text,
                zone_id=bot.current_zone_id,
                title="",
                npc_hint=bot.bot_id,
                generated_turn=turn,
            )


def _handle_guild_tick(result: dict) -> None:
    for r in result.get("results", []):
        if r.success and r.narrative:
            renderer.console.print(f"  [dim_text][ {r.narrative} ][/dim_text]")


def _handle_world_event(engine: "GameEngine", result: dict, rtype: str) -> None:
    event_text = result.get("event_text", "").strip()
    r_zone_id  = result.get("zone_id", "")
    title      = result.get("title", "")
    npc_hint   = result.get("npc_hint", "")
    if not event_text:
        return

    if feature("world_db") and engine.state.world_db:
        engine.state.world_db.store_world_event(
            event_type=rtype,
            event_text=event_text,
            zone_id=r_zone_id or engine.state.current_scene_id,
            title=title,
            npc_hint=npc_hint,
            generated_turn=engine.state.player.turn_count,
        )

    if rtype == "world_event":
        renderer.console.print(
            f"\n  [system_msg][ WORLD ][/system_msg]  [scene_text]{event_text}[/scene_text]"
        )
    elif rtype == "rumor":
        renderer.console.print(
            f"\n  [dim_text][ RUMOUR ][/dim_text]  [italic scene_text]{event_text}[/italic scene_text]"
        )
    elif rtype == "lore_entry":
        renderer.console.print(
            f"\n  [gold][ LORE ][/gold]  [scene_text]{event_text}[/scene_text]"
        )
    elif rtype == "area_activity":
        renderer.console.print(
            f"\n  [dim_text][ {(r_zone_id or 'nearby').upper()} ][/dim_text]  "
            f"[scene_text]{event_text}[/scene_text]"
        )
