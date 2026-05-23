"""
Decide what AI content to generate in the background each turn.

Extracted from ``core/game_engine.py``. ``maybe_submit_tasks(engine)`` runs
once per turn from the main loop. It:

1. Refreshes the BG worker's autonomous context (every turn — cheap).
2. On every Nth turn (``BG_GEN_INTERVAL``), submits a focused batch:
   - A world event for the current zone (always).
   - One *deeper* content type rotating across: AI quest, zone narrative
     refresh, local rumour, lore entry, area activity. Rotation cycles
     deterministically off the turn number so the player sees variety.
3. On every 3rd batch, submits one NPC-branch enrichment task.
4. On every ``GUILD_SIM_INTERVAL`` turn, submits a guild simulation tick.
5. Every batch, submits a bot decision per active bot.

Each helper here is named so adding a new content type is one new
``_submit_*`` plus one new branch in the rotation.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import BG_GEN_INTERVAL, GUILD_SIM_INTERVAL, feature

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


def maybe_submit_tasks(engine: "GameEngine") -> None:
    """Per-turn entry point. No-op when BG generation is disabled."""
    if not engine._bg_generator or not engine.state:
        return

    ctx = _build_context(engine)

    # Always refresh the autonomous worker's context — cheap, makes the
    # worker thread's idle-tick generation use up-to-date zone info.
    engine._bg_generator.update_context(
        zone_id=ctx["zone_id"],
        zone_name=ctx["zone_name"],
        player_level=ctx["player_level"],
        flags=ctx["context_flags"][:10],
        turn=ctx["turn"],
    )

    # Only fire the heavier batch every BG_GEN_INTERVAL turns (and on turn 1).
    turn = ctx["turn"]
    if turn < 1 or (turn > 1 and turn % BG_GEN_INTERVAL != 0):
        return

    _submit_world_event(engine, ctx)
    _submit_rotation_slot(engine, ctx)
    _maybe_submit_npc_branch(engine, ctx)
    _maybe_submit_guild_tick(engine, ctx)
    _maybe_submit_bot_decisions(engine, ctx)


# ── Context shared across submitters ─────────────────────────────────────────

def _build_context(engine: "GameEngine") -> dict:
    player    = engine.state.player
    zone_id   = engine.state.current_scene_id
    scene     = engine.scene_registry.get(zone_id)
    zone_name = scene.title if scene else zone_id.replace("_", " ").title()
    flags     = [k for k in player.flags if not k.startswith("_")][:8]
    return {
        "player":        player,
        "player_level":  player.level,
        "turn":          player.turn_count,
        "zone_id":       zone_id,
        "zone_name":     zone_name,
        "context_flags": flags,
    }


# ── Per-batch submitters ─────────────────────────────────────────────────────

def _submit_world_event(engine: "GameEngine", ctx: dict) -> None:
    """Every batch: a fresh world-event tied to the player's current zone."""
    engine._bg_generator.submit_world_event(
        zone_id=ctx["zone_id"],
        zone_name=ctx["zone_name"],
        player_level=ctx["player_level"],
        context_flags=ctx["context_flags"],
    )


def _submit_rotation_slot(engine: "GameEngine", ctx: dict) -> None:
    """Rotate through 5 deeper content types based on the turn number.

    Slot 0 = AI quest, 1 = zone narrative refresh, 2 = local rumour,
    3 = lore entry, 4 = area-activity texture. The rotation cycles
    deterministically so any given player sees variety, but two players
    on the same turn pick the same slot (helps with shared-save QA).
    """
    rotation = (ctx["turn"] // max(BG_GEN_INTERVAL, 1)) % 5

    if rotation == 0:
        engine._bg_generator.submit_quest(
            zone_id=ctx["zone_id"],
            npc_hint="a contact in the area",
            player=ctx["player"],
        )
    elif rotation == 1:
        # Mark this zone as having had narrative refresh submitted (used
        # downstream to avoid spamming the same zone on tight intervals).
        flag = f"_bg_narrative_submitted:{ctx['zone_id']}"
        if not ctx["player"].has_flag(flag):
            ctx["player"].set_flag(flag)
        engine._bg_generator.submit_zone_narrative(
            ctx["zone_id"], ctx["zone_name"], ctx["context_flags"],
        )
    elif rotation == 2:
        engine._bg_generator.submit_rumor(
            zone_id=ctx["zone_id"],
            context_flags=ctx["context_flags"],
            player_level=ctx["player_level"],
        )
    elif rotation == 3:
        engine._bg_generator.submit_lore_entry(
            context_flags=ctx["context_flags"],
            player_flags=ctx["context_flags"],
        )
    else:
        engine._bg_generator.submit_area_activity(
            zone_id=ctx["zone_id"],
            zone_name=ctx["zone_name"],
            context_flags=ctx["context_flags"],
        )


def _maybe_submit_npc_branch(engine: "GameEngine", ctx: dict) -> None:
    """Every 3rd batch: enrich one NPC's dialogue tree."""
    if ctx["turn"] % (BG_GEN_INTERVAL * 3) != 0:
        return
    if not (feature("npc_system") and engine.npc_registry):
        return
    player = ctx["player"]
    player_profile = {
        "level": ctx["player_level"],
        "alignment": player.alignment,
        "active_class": player.active_class or player.base_class or "Unclassified",
    }
    for npc_id in ("torven_blacksmith", "mira_innkeeper", "sylara_guildmaster"):
        npc = engine.npc_registry.get(npc_id)
        if npc:
            engine._bg_generator.submit_npc_branch(npc_id, npc.name, player_profile)
            return


def _maybe_submit_guild_tick(engine: "GameEngine", ctx: dict) -> None:
    """Every GUILD_SIM_INTERVAL turns: run a guild simulation tick in the BG."""
    if not (feature("guild_system") and engine.guild_registry):
        return
    if ctx["turn"] % GUILD_SIM_INTERVAL != 0:
        return
    engine._bg_generator.submit_guild_tick(
        world_db=engine.state.world_db,
        guild_registry=engine.guild_registry,
    )


def _maybe_submit_bot_decisions(engine: "GameEngine", ctx: dict) -> None:
    """Every batch: submit a decision request for each active bot."""
    if not (feature("bot_system") and engine._bot_manager):
        return
    world_context = {"player_zone": ctx["zone_id"], "turn": ctx["turn"]}
    for bot in engine._bot_manager.all():
        bot_profile = {
            "name": bot.name,
            "personality_seed": bot.personality_seed,
            "current_goal": bot.current_goal,
            "current_zone_id": bot.current_zone_id,
        }
        engine._bg_generator.submit_bot_decision(bot.bot_id, bot_profile, world_context)
