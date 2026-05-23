"""
Free-function versions of the option-building and trigger-processing logic
that used to live as methods on Scene. Extracted so non-scene callers
(quest_system, npc_system) don't have to instantiate a fake ``Scene("__npc__",
{"nodes": {}})`` just to reach them.

Scene keeps thin delegate methods (``Scene._build_option`` / ``Scene.process_triggers``)
that call straight through here, so existing scene code is unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from config import feature

if TYPE_CHECKING:
    from core.state_manager import GameState
    from scenes.scene_base import SceneOption


def build_option(raw: dict[str, Any], state: "GameState") -> "SceneOption":
    """Evaluate a raw option dict against current player/world state.

    Returns a fully-populated SceneOption with locked / lock_reason /
    hint_text / should_hide fields set according to the requires block.
    """
    # Import inside the function to avoid a circular import with scene_base.
    from scenes.scene_base import SceneOption

    player = state.player
    requires = raw.get("requires") or {}
    min_stats = requires.get("min_stats", {})
    required_items = requires.get("items", [])
    required_flags = requires.get("flags", [])
    absent_flags = requires.get("flags_absent", [])
    align_min = requires.get("alignment_min")
    align_max = requires.get("alignment_max")
    raw_stat_gates = requires.get("stat_gates", [])

    locked = False
    lock_reason = ""
    hint_text = ""
    should_hide = False

    # ── 1. Basic stat checks ────────────────────────────────────────────────
    for stat, value in min_stats.items():
        stat_val = getattr(player.stats, stat, 0)
        if stat_val < value:
            locked = True
            lock_reason = f"Requires {stat} ≥ {value}"
            break

    # ── 2. Item checks ──────────────────────────────────────────────────────
    if not locked:
        for item_id in required_items:
            if not player.has_item(item_id):
                locked = True
                lock_reason = "Missing required item"
                break

    # ── 3. Flag checks ──────────────────────────────────────────────────────
    if not locked:
        for flag in required_flags:
            if not player.has_flag(flag):
                locked = True
                lock_reason = "Condition not met"
                break

    # ── 3b. Absent-flag checks ──────────────────────────────────────────────
    if not should_hide:
        for flag in absent_flags:
            if player.has_flag(flag):
                should_hide = True
                break

    # ── 4. Alignment checks ─────────────────────────────────────────────────
    if not locked and feature("alignment_system"):
        if align_min is not None or align_max is not None:
            from systems.alignment_system import check_alignment_gate
            passed, reason = check_alignment_gate(player, align_min, align_max)
            if not passed:
                locked = True
                lock_reason = reason

    # ── 5. Advanced stat gates ──────────────────────────────────────────────
    if not locked and feature("stat_gating") and raw_stat_gates:
        from systems.stat_gate import build_gate_from_dict, evaluate_gate
        for gate_dict in raw_stat_gates:
            gate = build_gate_from_dict(gate_dict)
            if gate is None:
                continue
            result = evaluate_gate(player, gate)
            if not result.passed:
                if result.should_hide:
                    should_hide = True
                    break
                if result.hint_text:
                    hint_text = result.hint_text
                locked = True
                lock_reason = result.lock_reason or "Condition not met"
                break

    return SceneOption(
        option_id=raw.get("option_id", "unknown"),
        label=raw.get("label", "???"),
        leads_to=raw.get("leads_to", "__stay__"),
        leads_to_node=raw.get("leads_to_node", "root"),
        expected=raw.get("expected", True),
        triggers=raw.get("triggers", []),
        requires_min_stats=min_stats,
        requires_items=required_items,
        requires_flags=required_flags,
        requires_alignment_min=align_min,
        requires_alignment_max=align_max,
        stat_gates=raw_stat_gates,
        locked=locked,
        lock_reason=lock_reason,
        hint_text=hint_text,
        should_hide=should_hide,
    )


def process_triggers(triggers: list[str], state: "GameState") -> None:
    """Apply scene-level triggers (flag, give_item, give_skill, give_gold,
    alignment, set_species, set_gender, rest_camp, rest_inn, give_food).

    Triggers the engine handles elsewhere (set_base_class, combat,
    scene_transition) are accepted and silently ignored here.
    """
    from core.event_bus import Event, bus

    for trigger in triggers:
        if trigger.startswith("flag:"):
            state.player.set_flag(trigger[5:])

        elif trigger.startswith("give_item:"):
            item_id = trigger[10:]
            state.player.add_item(item_id)
            bus.publish(Event("ITEM_FOUND", {"item_id": item_id}))

        elif trigger.startswith("give_skill:"):
            skill_id = trigger[11:]
            if skill_id not in state.player.skills:
                skill = state.skill_registry.get(skill_id) if state.skill_registry else None
                if skill is None:
                    from entities.skill import Skill
                    from entities.enums import Rarity
                    skill = Skill(
                        skill_id=skill_id,
                        name=skill_id.replace("_", " ").title(),
                        rarity=Rarity.UNCOMMON,
                        description="An ability awakened through unconventional experience.",
                        is_ai_generated=True,
                    )
                    if state.skill_registry:
                        state.skill_registry.register(skill)
                    if feature("world_db") and getattr(state, "world_db", None):
                        try:
                            state.world_db.store_ai_skill(
                                skill_id=skill_id,
                                definition=skill.model_dump(mode="json"),
                                source="give_skill_trigger",
                                generated_turn=state.player.turn_count,
                            )
                        except Exception:
                            pass
                state.player.skills.append(skill_id)
                bus.publish(Event("SKILL_ACQUIRED", {
                    "skill_id": skill_id,
                    "skill_name": skill.name if skill else skill_id.replace("_", " ").title(),
                    "rarity": skill.rarity.value if skill else "UNCOMMON",
                }))

        elif trigger.startswith("give_gold:"):
            state.player.gold += int(trigger[10:])

        elif trigger.startswith("alignment:"):
            if feature("alignment_system"):
                raw_val = trigger[10:]
                try:
                    delta = float(raw_val)
                    from systems.alignment_system import apply_alignment_shift
                    apply_alignment_shift(state.player, delta, reason=f"trigger:{trigger}")
                except ValueError:
                    pass

        elif trigger.startswith("set_gender:"):
            state.player.gender = trigger[11:]

        elif trigger.startswith("set_species:"):
            state.player.set_flag(f"_pending_species:{trigger[12:]}")

        elif trigger.startswith("set_background:"):
            state.player.set_flag(f"_pending_background:{trigger[15:]}")

        elif trigger.startswith("rest_camp:"):
            rest_type = trigger[10:]
            if rest_type == "full":
                state.player.current_hp = state.player.max_hp
                state.player.current_mp = state.player.max_mp
                bus.publish(Event("SYSTEM_MSG", {
                    "message": "You rest at camp. HP and MP fully restored."
                }))
            elif rest_type == "short":
                restore_hp = state.player.max_hp // 2
                restore_mp = state.player.max_mp // 2
                state.player.current_hp = min(
                    state.player.max_hp,
                    state.player.current_hp + restore_hp,
                )
                state.player.current_mp = min(
                    state.player.max_mp,
                    state.player.current_mp + restore_mp,
                )
                bus.publish(Event("SYSTEM_MSG", {
                    "message": "You take a short rest. HP and MP partially restored."
                }))

        elif trigger.startswith("rest_inn:"):
            try:
                cost = int(trigger[9:])
            except ValueError:
                cost = 0
            if state.player.gold >= cost:
                state.player.gold -= cost
                state.player.current_hp = state.player.max_hp
                state.player.current_mp = state.player.max_mp
                state.player.set_flag("well_rested")
                from systems.buff_system import apply_buff
                for stat in ("STR", "INT", "AGI", "VIT", "END"):
                    apply_buff(state.player, stat, 1, 30, "well_rested")
                bus.publish(Event("SYSTEM_MSG", {
                    "message": "You rest through the night. HP and MP fully restored. Well-rested buff applied (+1 all combat stats, 30 turns)."
                }))
            else:
                bus.publish(Event("WARNING", {"message": "Not enough gold to pay for the room."}))

        elif trigger.startswith("give_food:"):
            item_id = trigger[10:]
            state.player.add_item(item_id, 1)
            bus.publish(Event("ITEM_FOUND", {"item_id": item_id, "item_name": item_id, "rarity": "COMMON"}))

        # ── Engine-delegated triggers (no-op here) ──────────────────────────
        elif trigger.startswith("set_base_class:"):
            pass
        elif trigger.startswith("combat:"):
            pass
        elif trigger.startswith("scene_transition:"):
            pass

    state.mark_dirty()
