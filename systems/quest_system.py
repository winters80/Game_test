"""
Quest System

Handles:
  - Starting quests from template_id + giver NPC
  - Polling active quests each turn for silent completions
  - Stage advancement with trigger firing
  - Quest completion (rewards) and failure
  - Quest log summaries for UI display
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from entities.quest import QuestTemplate, QuestStage, QuestRegistry
    from core.state_manager import GameState


# ── Start ─────────────────────────────────────────────────────────────────────

def start_quest(
    template_id: str,
    giver_npc_id: str | None,
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> str | None:
    """
    Instantiate a quest from its template. Returns the instance_id, or None
    if the template is not found or the quest is already active.
    """
    template = quest_registry.get(template_id)
    if not template:
        return None

    # Don't re-start an already active quest for the same template
    for qid in state.player.active_quest_ids:
        existing = state.world_db.get_quest(qid) if state.world_db else None
        if existing and existing.get("template_id") == template_id:
            return qid  # already running

    instance_id = str(uuid.uuid4())[:8]  # short readable id
    initial_stage = template.initial_stage_id

    if state.world_db:
        state.world_db.add_quest(
            instance_id=instance_id,
            template_id=template_id,
            initial_state=initial_stage,
            giver_npc_id=giver_npc_id,
            turn_number=state.turn_number,
            ai_context={
                "player_level": state.player.level,
                "player_class": state.player.base_class or "unclassified",
                "player_species": state.player.species_id or "unknown",
                "alignment": state.player.alignment,
            },
        )

    state.player.add_quest(instance_id)

    bus.publish(Event("QUEST_STARTED", {
        "quest_id": instance_id,
        "template_id": template_id,
        "title": template.title,
    }))

    return instance_id


# ── Stage evaluation ──────────────────────────────────────────────────────────

def check_stage_completion(
    instance_id: str,
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> bool:
    """Return True if the quest's current stage completion condition is met."""
    if state.world_db is None:
        return False

    row = state.world_db.get_quest(instance_id)
    if not row or not row.get("is_active"):
        return False

    template = quest_registry.get(row["template_id"])
    if not template:
        return False

    stage = template.get_stage(row["current_state"])
    if not stage:
        return False

    return _evaluate_condition(stage.completion_condition, state)


def _evaluate_condition(condition: dict, state: "GameState") -> bool:
    """Evaluate a single completion_condition dict against current player/world state."""
    if not condition:
        return False

    if "has_item" in condition:
        return state.player.has_item(condition["has_item"])

    if "has_flag" in condition:
        return state.player.has_flag(condition["has_flag"])

    if "world_flag" in condition:
        return state.has_world_flag(condition["world_flag"])

    return False


# ── Advancement ───────────────────────────────────────────────────────────────

def advance_quest(
    instance_id: str,
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> bool:
    """
    Advance the quest to its next stage, firing on_advance_triggers.
    If there is no next stage, completes the quest with outcome 'success'.
    Returns True if the quest was completed, False if it simply advanced.
    """
    if state.world_db is None:
        return False

    row = state.world_db.get_quest(instance_id)
    if not row:
        return False

    template = quest_registry.get(row["template_id"])
    if not template:
        return False

    stage = template.get_stage(row["current_state"])
    if not stage:
        return False

    # Fire on_advance_triggers for this stage
    if stage.on_advance_triggers:
        from scenes.scene_base import Scene
        dummy = Scene("__quest__", {"nodes": {}})
        dummy.process_triggers(stage.on_advance_triggers, state)

    # Terminal stage → complete
    if stage.next_stage_id is None:
        complete_quest(instance_id, "success", state, quest_registry)
        return True

    # Advance to next stage
    state.world_db.advance_quest(instance_id, stage.next_stage_id)
    bus.publish(Event("QUEST_ADVANCED", {
        "quest_id": instance_id,
        "template_id": row["template_id"],
        "title": template.title,
        "new_stage": stage.next_stage_id,
    }))
    return False


# ── Completion ────────────────────────────────────────────────────────────────

def complete_quest(
    instance_id: str,
    outcome: str,
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> None:
    """Apply rewards and fire QUEST_COMPLETED event."""
    if state.world_db:
        row = state.world_db.get_quest(instance_id)
        template = quest_registry.get(row["template_id"]) if row else None
        state.world_db.complete_quest(instance_id, outcome, state.turn_number)
    else:
        row = None
        template = None

    state.player.complete_quest(instance_id)

    if template and outcome == "success":
        _apply_rewards(template, state)
        bus.publish(Event("QUEST_COMPLETED", {
            "quest_id": instance_id,
            "title": template.title,
            "reward_gold": template.reward_gold,
            "reward_xp": template.reward_xp,
        }))
    else:
        title = template.title if template else instance_id
        bus.publish(Event("QUEST_FAILED", {
            "quest_id": instance_id,
            "title": title,
        }))


def _apply_rewards(template: "QuestTemplate", state: "GameState") -> None:
    """Grant gold, XP, items, flags, and alignment from a completed quest."""
    from systems import level_system
    from systems.alignment_system import apply_alignment_shift
    from core.event_bus import Event, bus

    player = state.player

    if template.reward_gold:
        player.gold += template.reward_gold * 100   # template stores gold, player stores copper

    if template.reward_xp:
        leveled_up = level_system.add_experience(player, template.reward_xp)
        if leveled_up:
            bus.publish(Event("LEVEL_UP", {
                "level": player.level,
                "stat_points": player.stat_points,
            }))

    for item_id in template.reward_items:
        player.add_item(item_id)
        bus.publish(Event("ITEM_FOUND", {"item_id": item_id, "item_name": item_id, "rarity": "COMMON"}))

    for flag in template.reward_flags:
        player.set_flag(flag)

    if template.alignment_reward:
        apply_alignment_shift(player, template.alignment_reward, reason=f"quest:{template.template_id}")

    # Faction standing rewards
    if template.faction_rewards and state.world_db:
        for faction_id, delta in template.faction_rewards.items():
            state.world_db.update_faction_standing(faction_id, delta)
            # Cache on player for fast reads
            player.faction_standing_cache[faction_id] = player.faction_standing_cache.get(faction_id, 0.0) + delta

    # Guild standing rewards (stored as faction_standing with guild_ prefix for simplicity)
    if template.guild_rewards and state.world_db:
        from config import feature
        if feature("guild_system"):
            for guild_id, delta in template.guild_rewards.items():
                state.world_db.update_faction_standing(f"guild_{guild_id}", delta)


def fail_quest(
    instance_id: str,
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> None:
    complete_quest(instance_id, "failed", state, quest_registry)


# ── Tick (called every turn) ──────────────────────────────────────────────────

def tick_quests(state: "GameState", quest_registry: "QuestRegistry") -> None:
    """
    Called each game loop turn. Checks all active quests for:
    - Silent stage completions (e.g. player picked up item without speaking to NPC)
    - Failure conditions
    - Time limits
    """
    if not state.player.active_quest_ids:
        return

    # Copy the list — advance_quest may mutate active_quest_ids
    for instance_id in list(state.player.active_quest_ids):
        _tick_single(instance_id, state, quest_registry)


def _tick_single(
    instance_id: str,
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> None:
    if state.world_db is None:
        return

    row = state.world_db.get_quest(instance_id)
    if not row or not row.get("is_active"):
        return

    template = quest_registry.get(row["template_id"])
    if not template:
        return

    # Check failure conditions first
    for cond in template.failure_conditions:
        if _evaluate_condition(cond, state):
            fail_quest(instance_id, state, quest_registry)
            return

    # Check time limit
    if template.time_limit_turns is not None:
        elapsed = state.turn_number - row.get("accepted_turn", 0)
        if elapsed > template.time_limit_turns:
            fail_quest(instance_id, state, quest_registry)
            return

    # Check stage completion
    if check_stage_completion(instance_id, state, quest_registry):
        advance_quest(instance_id, state, quest_registry)


# ── Quest log for UI ──────────────────────────────────────────────────────────

def generate_ai_quest(
    giver_npc_id: str,
    npc_name: str,
    npc_role: str,
    state: "GameState",
    ai_service: object,
) -> str | None:
    """
    Use AI to generate a dynamic quest and start it. Returns instance_id or None.
    Falls back gracefully if AI is unavailable.

    ``ai_service`` is an AIService instance. None or an unavailable service
    returns None without exception.
    """
    if ai_service is None or not getattr(ai_service, "is_available", False):
        return None

    template = ai_service.generate_quest(
        state.player, giver_npc_id, npc_name, npc_role,
    )
    if not template:
        return None

    # Store AI quest definition in world_db
    instance_id = str(uuid.uuid4())[:8]
    initial_stage = template.initial_stage_id

    if state.world_db:
        state.world_db.add_quest(
            instance_id=instance_id,
            template_id=template.template_id,
            initial_state=initial_stage,
            giver_npc_id=giver_npc_id,
            turn_number=state.turn_number,
            ai_context={"ai_generated": True},
        )
        state.world_db.store_ai_quest(instance_id, template.model_dump())

    state.player.add_quest(instance_id)

    bus.publish(Event("QUEST_STARTED", {
        "quest_id": instance_id,
        "template_id": template.template_id,
        "title": template.title,
    }))

    return instance_id


def get_active_quest_summaries(
    state: "GameState",
    quest_registry: "QuestRegistry",
) -> list[dict]:
    """Return display-ready dicts for all active quests."""
    summaries = []
    if not state.player.active_quest_ids:
        return summaries

    for instance_id in state.player.active_quest_ids:
        row = state.world_db.get_quest(instance_id) if state.world_db else None
        if not row:
            continue
        template = quest_registry.get(row["template_id"])
        if not template:
            continue
        stage = template.get_stage(row["current_state"])
        summaries.append({
            "instance_id": instance_id,
            "title": template.title,
            "objective": stage.objective_text if stage else "???",
            "turns_active": state.turn_number - row.get("accepted_turn", 0),
        })
    return summaries
