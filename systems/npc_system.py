"""
NPC System

Handles:
  - Registering NPC instances in world_db on first encounter
  - Resolving which dialogue root node to enter based on disposition
  - Applying INT/Perception smart_observation and perception_reveal filters
  - Evaluating dialogue option requires blocks (reuses scene_base gate logic)
  - Applying disposition changes, triggers, memory recording
  - Memory compression when event count hits NPC_MEMORY_COMPRESS_AT
  - Checking NPC visibility from appears_requires
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import NPC_MEMORY_MAX_EVENTS, NPC_MEMORY_COMPRESS_AT, NPC_MEMORY_KEEP_RECENT

if TYPE_CHECKING:
    from entities.npc import NPCTemplate, NPCDialogueNode, NPCDialogueOption
    from entities.player import Player
    from core.state_manager import GameState
    from scenes.scene_base import SceneOption


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ResolvedNode:
    """A dialogue node with smart_observation/perception_reveal already applied."""
    node_id: str
    display_text: str           # npc_text + any appended stat reveals
    options: list["SceneOption"]    # gate-evaluated, hidden options filtered out


@dataclass
class ApplyResult:
    """What the engine still needs to handle after option effects are applied."""
    engine_triggers: list[str] = field(default_factory=list)  # start_quest:X etc.
    memory_recorded: bool = False


# ── Visibility ────────────────────────────────────────────────────────────────

def check_npc_visible(npc: "NPCTemplate", state: "GameState") -> bool:
    """
    Return True if the NPC should appear in the current scene.
    Evaluates appears_requires: supports standard 'flags'/'items' keys
    plus 'any_of_flags' and 'any_of_items' (player needs at least one).
    """
    player = state.player
    req = npc.appears_requires

    if not req:
        return True

    # Standard ALL-required flags
    for flag in req.get("flags", []):
        if not player.has_flag(flag):
            return False

    # Standard ALL-required items
    for item_id in req.get("items", []):
        if not player.has_item(item_id):
            return False

    # any_of_flags / any_of_items — when either key is present they form a
    # combined OR pool: at least one condition from either list must be met.
    any_flags = req.get("any_of_flags", [])
    any_items = req.get("any_of_items", [])
    if any_flags or any_items:
        combined_pass = (
            any(player.has_flag(f) for f in any_flags)
            or any(player.has_item(i) for i in any_items)
        )
        if not combined_pass:
            return False

    return True


# ── Instance management ───────────────────────────────────────────────────────

def ensure_npc_instance(npc: "NPCTemplate", state: "GameState") -> None:
    """
    Register the NPC in world_db if this is the first encounter.
    Safe to call every time — ON CONFLICT DO NOTHING in upsert_npc.
    """
    if state.world_db is None:
        return
    state.world_db.upsert_npc(
        npc_id=npc.npc_id,
        template_id=npc.template_id,
        zone_id=npc.zone_id,
        role=npc.role,
    )
    # Only set starting disposition if the row was just created
    row = state.world_db.get_npc(npc.npc_id)
    if row and row.get("disposition", 0.0) == 0.0 and npc.starting_disposition != 0.0:
        state.world_db.update_npc_disposition(npc.npc_id, npc.starting_disposition)


def get_npc_disposition(npc: "NPCTemplate", state: "GameState") -> float:
    """Return the NPC's current disposition from world_db, falling back to template default."""
    if state.world_db is None:
        return npc.starting_disposition
    row = state.world_db.get_npc(npc.npc_id)
    return row["disposition"] if row else npc.starting_disposition


# ── Disposition hook resolution ───────────────────────────────────────────────

def get_disposition_hook(npc: "NPCTemplate", disposition: float) -> str:
    """
    Return the entry node_id for this disposition level.
    Bracket: hostile < -30, friendly > +40, default = everything else.
    Falls back to 'root' if a hook is missing.
    """
    hooks = npc.disposition_hooks
    if disposition < -30.0 and "hostile" in hooks:
        return hooks["hostile"]
    if disposition > 40.0 and "friendly" in hooks:
        return hooks["friendly"]
    return hooks.get("default", "root")


# ── Node resolution ───────────────────────────────────────────────────────────

def resolve_node(
    npc: "NPCTemplate",
    node_id: str,
    state: "GameState",
    disposition: float,
) -> ResolvedNode:
    """
    Return a ResolvedNode: display text with stat reveals applied, and
    gate-evaluated SceneOptions (hidden options filtered out).
    """
    node = npc.dialogue_nodes.get(node_id)
    if node is None:
        # Fallback: try root
        node = npc.dialogue_nodes.get("root")
    if node is None:
        return ResolvedNode(node_id=node_id, display_text="...", options=[])

    player = state.player
    text = node.npc_text

    # Append smart_observation if INT meets threshold
    if node.smart_observation and node.smart_threshold > 0:
        if player.stats.INT >= node.smart_threshold:
            text = text + "\n\n" + node.smart_observation

    # Append perception_reveal if Perception meets threshold
    if node.perception_reveal and node.perception_threshold > 0:
        if player.perception >= node.perception_threshold:
            text = text + "\n\n" + node.perception_reveal

    # Evaluate each option against player's stats/items/flags
    evaluated = []
    for opt in node.options:
        scene_opt = _evaluate_dialogue_option(opt, state, disposition)
        if not scene_opt.should_hide:
            evaluated.append(scene_opt)

    return ResolvedNode(node_id=node_id, display_text=text, options=evaluated)


def _evaluate_dialogue_option(
    opt: "NPCDialogueOption",
    state: "GameState",
    disposition: float,
) -> "SceneOption":
    """
    Adapt an NPCDialogueOption into a SceneOption using scene_base's gate logic.
    Also checks min_disposition requirement.
    """
    from scenes.scene_base import Scene

    # Build a synthetic raw option dict compatible with _build_option
    raw = {
        "option_id": opt.option_id,
        "label": opt.label,
        "leads_to": "__stay__",
        "leads_to_node": opt.leads_to_node,
        "expected": True,
        "triggers": opt.triggers,
        "requires": opt.requires,
    }

    # Use a minimal Scene to access _build_option
    dummy_scene = Scene("__npc__", {"nodes": {}})
    scene_opt = dummy_scene._build_option(raw, state)

    # Additional disposition check (not part of scene gate system)
    min_disp = opt.requires.get("min_disposition")
    if min_disp is not None and disposition < float(min_disp):
        scene_opt.locked = True
        scene_opt.lock_reason = f"Requires higher disposition"

    return scene_opt


# ── Applying option effects ───────────────────────────────────────────────────

def apply_option_effects(
    opt: "NPCDialogueOption",
    npc: "NPCTemplate",
    state: "GameState",
) -> ApplyResult:
    """
    Apply effects of a chosen dialogue option:
    1. Disposition change
    2. Scene-compatible triggers (flag, give_item, give_gold, alignment, give_skill)
    3. Returns engine-delegated triggers (start_quest, give_npc_memory)
    4. Records memory event if specified
    """
    result = ApplyResult()

    # 1. Disposition change
    if opt.disposition_change != 0.0 and state.world_db is not None:
        state.world_db.update_npc_disposition(npc.npc_id, opt.disposition_change)

    # 2. Scene-compatible triggers
    scene_triggers = []
    engine_triggers = []

    for trigger in opt.triggers:
        if trigger.startswith("start_quest:"):
            engine_triggers.append(trigger)
        elif trigger.startswith("give_npc_memory:"):
            engine_triggers.append(trigger)
        else:
            scene_triggers.append(trigger)

    if scene_triggers:
        from scenes.scene_base import Scene
        dummy = Scene("__npc__", {"nodes": {}})
        dummy.process_triggers(scene_triggers, state)

    result.engine_triggers = engine_triggers

    # 3. Memory recording
    if opt.memory_event_type and state.world_db is not None:
        record_interaction(
            npc_id=npc.npc_id,
            event_type=opt.memory_event_type,
            summary=opt.memory_summary or f"[{opt.option_id}]",
            state=state,
        )
        result.memory_recorded = True

    return result


# ── Memory management ─────────────────────────────────────────────────────────

def record_interaction(
    npc_id: str,
    event_type: str,
    summary: str,
    state: "GameState",
) -> None:
    """Write a memory event and compress if threshold is reached."""
    if state.world_db is None:
        return
    state.world_db.add_npc_memory(
        npc_id=npc_id,
        event_type=event_type,
        summary=summary,
        turn_number=state.turn_number,
        alignment_snapshot=state.player.alignment,
    )
    maybe_compress_memory(npc_id, state)


def maybe_compress_memory(npc_id: str, state: "GameState") -> None:
    """
    If uncompressed memory event count >= NPC_MEMORY_COMPRESS_AT,
    compress the oldest (count - NPC_MEMORY_KEEP_RECENT) events into a
    single summary row.
    """
    if state.world_db is None:
        return

    count = state.world_db.count_npc_memory(npc_id)
    if count < NPC_MEMORY_COMPRESS_AT:
        return

    # Get all memory rows to find the cutoff id
    all_rows = state.world_db.get_npc_memory(npc_id, limit=count)
    # Keep the most recent NPC_MEMORY_KEEP_RECENT rows uncompressed
    rows_to_compress = all_rows[: count - NPC_MEMORY_KEEP_RECENT]
    if not rows_to_compress:
        return

    summary_lines = [f"[{r['event_type']}] {r['summary']}" for r in rows_to_compress]
    compressed_text = " | ".join(summary_lines)
    cutoff_id = rows_to_compress[-1]["id"]

    state.world_db.compress_npc_memory(npc_id, compressed_text, cutoff_id)


# ── Quest seed checking ───────────────────────────────────────────────────────

def get_available_quest_seeds(
    npc: "NPCTemplate",
    state: "GameState",
) -> list[Any]:
    """
    Return quest seeds that the NPC can currently offer to this player.
    Filters out already-given quests and disposition/flag requirements.
    """
    available = []
    disposition = get_npc_disposition(npc, state)
    player = state.player

    for seed in npc.quest_seeds:
        # Already given check
        if seed.already_given_flag and player.has_flag(seed.already_given_flag):
            continue
        # Disposition minimum
        if disposition < seed.trigger_disposition_min:
            continue
        # Trigger flag (if set, player must have it)
        if seed.trigger_flag and not player.has_flag(seed.trigger_flag):
            continue
        available.append(seed)

    return available
