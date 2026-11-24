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

    return _evaluate_condition(stage.completion_condition, state,
                               turnin_flag=turn_in_flag(template.template_id, stage.stage_id))


def turn_in_flag(template_id: str, stage_id: str) -> str:
    """Set by dialogue when the player reports a ``turn_in`` stage."""
    return f"turnin_{template_id}_{stage_id}"


def item_quantity(player, item_id: str) -> int:
    return sum(s.quantity for s in player.inventory if s.item_id == item_id)


def _evaluate_condition(condition: dict, state: "GameState", turnin_flag: str = "",
                        skip_turn_in: bool = False) -> bool:
    """Evaluate a completion_condition dict against current player/world state.
    ``skip_turn_in`` treats turn_in parts as met (used to decide whether a
    « Report » option should be offered)."""
    if not condition:
        return False

    if "all" in condition:
        parts = condition["all"] or []
        return bool(parts) and all(_evaluate_condition(c, state, turnin_flag, skip_turn_in) for c in parts)

    if "any" in condition:
        return any(_evaluate_condition(c, state, turnin_flag, skip_turn_in) for c in condition["any"] or [])

    if "turn_in" in condition:
        return skip_turn_in or (bool(turnin_flag) and state.player.has_flag(turnin_flag))

    if "has_item" in condition:
        return state.player.has_item(condition["has_item"])

    if "has_items" in condition:
        return all(item_quantity(state.player, i) >= int(n) for i, n in condition["has_items"].items())

    if "has_flag" in condition:
        return state.player.has_flag(condition["has_flag"])

    if "world_flag" in condition:
        return state.has_world_flag(condition["world_flag"])

    if "min_level" in condition:
        return state.player.level >= int(condition["min_level"])

    return False


def _turn_in_npcs(condition: dict) -> set[str]:
    if not condition:
        return set()
    if "turn_in" in condition:
        return {condition["turn_in"]}
    out: set[str] = set()
    for key in ("all", "any"):
        for c in condition.get(key) or []:
            out |= _turn_in_npcs(c)
    return out


def wanted_flags(condition: dict) -> set[str]:
    """Every ``has_flag`` a condition mentions (inside ``all`` / ``any`` too)."""
    if not condition:
        return set()
    out = {condition["has_flag"]} if condition.get("has_flag") else set()
    for key in ("all", "any"):
        for c in condition.get(key) or []:
            out |= wanted_flags(c)
    return out


def gate_level_needed(flags, player, gate_levels: dict[str, int]) -> int:
    """The level at which the Gates behind ``flags`` (``cleared_<gate>`` /
    ``slew_<gate>_<occupant>``) open, if that's above the player's; else 0.
    ``gate_levels`` maps gate id → the level the Registry lets you in."""
    need = 0
    for f in flags:
        if player.has_flag(f):
            continue
        body = f[len("cleared_"):] if f.startswith("cleared_") else f[len("slew_"):] if f.startswith("slew_") else ""
        gate = body if body in gate_levels else next((g for g in gate_levels if body.startswith(g + "_")), "")
        if gate:
            need = max(need, gate_levels[gate])
    return need if need > player.level else 0


def reportable_quests(npc_id: str, state: "GameState", quest_registry: "QuestRegistry") -> list[tuple]:
    """Active quests whose current stage is ready to report to ``npc_id``:
    (instance_id, template, stage) for each."""
    out = []
    if state.world_db is None:
        return out
    for qid in list(state.player.active_quest_ids):
        row = state.world_db.get_quest(qid)
        if not row or not row.get("is_active"):
            continue
        template = quest_registry.get(row["template_id"])
        stage = template.get_stage(row["current_state"]) if template else None
        if stage is None or npc_id not in _turn_in_npcs(stage.completion_condition):
            continue
        flag = turn_in_flag(template.template_id, stage.stage_id)
        if state.player.has_flag(flag):
            continue
        if _evaluate_condition(stage.completion_condition, state, flag, skip_turn_in=True):
            out.append((qid, template, stage))
    return out


def report_quest(instance_id: str, state: "GameState", quest_registry: "QuestRegistry") -> bool:
    """The player reports in: mark the turn-in and advance if the stage is now
    complete. Returns True if the quest advanced or completed."""
    row = state.world_db.get_quest(instance_id) if state.world_db else None
    template = quest_registry.get(row["template_id"]) if row else None
    stage = template.get_stage(row["current_state"]) if template else None
    if stage is None:
        return False
    state.player.set_flag(turn_in_flag(template.template_id, stage.stage_id))
    if check_stage_completion(instance_id, state, quest_registry):
        advance_quest(instance_id, state, quest_registry)
        return True
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
        from scenes.option_logic import process_triggers as _pt
        _pt(stage.on_advance_triggers, state)

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
    from config import COPPER_PER_GOLD
    from systems import level_system
    from systems.alignment_system import apply_alignment_shift
    from core.event_bus import Event, bus

    player = state.player

    if template.reward_gold:
        # template.reward_gold is denominated in GOLD PIECES (see QuestTemplate
        # field doc). Player wallet stores copper. Convert via COPPER_PER_GOLD.
        player.gold += template.reward_gold * COPPER_PER_GOLD

    if template.reward_xp:
        # Pull registries from state if attached — auto-grants next class
        # skill on level-up (F9). add_experience itself publishes LEVEL_UP.
        leveled_up = level_system.add_experience(
            player, template.reward_xp,
            class_registry=getattr(state, "class_registry", None),
            skill_registry=getattr(state, "skill_registry", None),
        )

    for item_id in template.reward_items:
        player.add_item(item_id)
        bus.publish(Event("ITEM_FOUND", {"item_id": item_id, "item_name": item_id, "rarity": "COMMON"}))

    for flag in template.reward_flags:
        player.set_flag(flag)

    # ── Skill rewards (AI-generated, contextual to this quest) ───────────
    # Each hint becomes a full Skill generated by the AI using the quest's
    # title + description as narrative anchor. Falls back to a deterministic
    # default when AI is offline so the player isn't shorted on rewards.
    if template.reward_skill_hints:
        _grant_quest_skill_rewards(template, state)

    if template.reward_skills:
        from scenes.option_logic import process_triggers as _pt
        _pt([f"give_skill:{sid}" for sid in template.reward_skills if sid not in player.skills], state)

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

    # Clamp the AI-proposed reward to the player-level economy. The prompt
    # tells the LLM the right range but we don't trust it — a hallucinating
    # model would otherwise drop 5000g on a level-1 player and break the
    # entire item economy in one quest.
    from systems.economy import clamp_quest_reward, infer_difficulty_from_stages
    difficulty = infer_difficulty_from_stages(len(template.stages))
    clamped_gold, clamped_xp = clamp_quest_reward(
        proposed_gold=template.reward_gold,
        proposed_xp=template.reward_xp,
        player_level=state.player.level,
        difficulty=difficulty,
    )
    template.reward_gold = clamped_gold
    template.reward_xp = clamped_xp

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
        step, total = chain_progress(template, quest_registry)
        ready = bool(stage) and any(
            q[0] == instance_id
            for npc_id in _turn_in_npcs(stage.completion_condition)
            for q in reportable_quests(npc_id, state, quest_registry))
        summaries.append({
            "instance_id": instance_id,
            "title": template.title,
            "objective": stage.objective_text if stage else "???",
            "turns_active": state.turn_number - row.get("accepted_turn", 0),
            "chain": f"{template.chain_kind or 'chain'} {step}/{total}" if total > 1 else "",
            "ready_to_report": ready,
            "giver_npc_id": template.giver_npc_id,
            "flags": wanted_flags(stage.completion_condition) if stage else set(),
        })
    return summaries


def chain_progress(template: "QuestTemplate", quest_registry: "QuestRegistry") -> tuple[int, int]:
    """(this quest's step, steps in its chain); (1, 1) for a standalone quest."""
    if not template.chain_id:
        return 1, 1
    total = sum(1 for q in quest_registry.all() if q.chain_id == template.chain_id)
    return template.chain_step, total


def available_work(state: "GameState", quest_registry: "QuestRegistry", npc_registry) -> list[tuple]:
    """Quests the player could pick up right now: (npc, template), lowest level first."""
    from systems.npc_system import get_available_quest_seeds

    out = []
    if npc_registry is None:
        return out
    for npc in npc_registry.all():
        if npc.is_ai_generated:
            continue
        for seed in get_available_quest_seeds(npc, state):
            tmpl = quest_registry.get(seed.quest_template_id)
            if tmpl is not None:
                out.append((npc, tmpl, seed.min_level))
    out.sort(key=lambda t: (t[2], t[1].chain_step > 1, t[1].title))
    return [(n, t) for n, t, _ in out]


def _known_skills_ctx(state: "GameState") -> dict:
    from scenes.option_logic import _known_skills_ctx as _k
    return _k(state.player, getattr(state, "skill_registry", None))


def _grant_quest_skill_rewards(template: "QuestTemplate", state: "GameState") -> None:
    """Generate (or fall back to a default for) each reward_skill_hint and
    add the result to the player. Registers each generated skill in the
    SkillRegistry so future combat lookups find it.
    """
    ai_service = getattr(state, "ai_service", None)
    skill_registry = getattr(state, "skill_registry", None)
    if skill_registry is None:
        return  # nothing to register against; would create dangling IDs

    # Inspect-aware: utility passive bumps description detail.
    has_inspect = False
    try:
        from systems.passive_system import get_passive_modifiers
        mods = get_passive_modifiers(state.player, skill_registry)
        has_inspect = "inspect" in mods.utility_tags
    except Exception:
        pass

    quest_ctx = {
        "quest_title": template.title,
        "quest_description": template.description[:240],
    }

    for hint in template.reward_skill_hints:
        skill = None
        if ai_service is not None and getattr(ai_service, "is_available", False):
            skill = ai_service.generate_skill(
                player=state.player,
                name_hint=hint,
                source="quest_reward",
                context={**quest_ctx, **_known_skills_ctx(state)},
                has_inspect=has_inspect,
            )
        if skill is None:
            # Deterministic fallback (no AI / generation failed)
            from entities.skill import Skill, SkillEffect
            from entities.enums import Rarity, EffectType
            slug = hint.lower().replace(" ", "_").replace("-", "_")[:40] or "quest_skill"
            skill = Skill(
                skill_id=slug,
                name=hint,
                rarity=Rarity.UNCOMMON,
                description=f"A technique earned from '{template.title}'.",
                skill_type="ACTIVE",
                mp_cost=6,
                cooldown_turns=2,
                effects=[SkillEffect(
                    effect_type=EffectType.DAMAGE,
                    scaling_stat="LCK", base_value=8.0,
                    scaling_coefficient=1.2,
                )],
                is_ai_generated=True,
            )

        skill_registry.register(skill)
        if skill.skill_id not in state.player.skills:
            state.player.skills.append(skill.skill_id)
            bus.publish(Event("SKILL_ACQUIRED", {
                "skill_id": skill.skill_id,
                "skill_name": skill.name,
                "rarity": skill.rarity.value,
            }))
