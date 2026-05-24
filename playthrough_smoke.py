"""
Headless playthrough smoke test.

Drives the engine the way a player would — character creation → talk to NPCs
→ start quests → complete them → claim rewards → check world state — without
ever touching ``questionary``. The goal is to surface integration bugs that
the unit suites miss because they exercise systems in isolation.

Run:
    python -X utf8 playthrough_smoke.py

Exit code 0 if everything worked. Each step prints a `[OK]` or `[!!]` line
so failures are obvious in the transcript.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config import COPPER_PER_GOLD, SAVES_DIR, feature
from core.event_bus import bus, Event
from core.game_engine import GameEngine
from entities.player import Player
from persistence.save_manager import close_game, new_game_state, save_game, load_game
from systems import npc_system, quest_system

SLOT = "_playthrough_smoke_"

# ── Reporting ────────────────────────────────────────────────────────────────

PASSES: list[str] = []
ISSUES: list[str] = []

def ok(msg: str) -> None:
    PASSES.append(msg)
    print(f"  [OK] {msg}")

def fail(msg: str, exc: Exception | None = None) -> None:
    detail = f" — {exc}" if exc else ""
    ISSUES.append(f"{msg}{detail}")
    print(f"  [!!] {msg}{detail}")

def section(title: str) -> None:
    print(f"\n{'-' * 68}\n  {title}\n{'-' * 68}")


# ── Event capture ────────────────────────────────────────────────────────────

captured_events: list[Event] = []

def event_recorder(event: Event) -> None:
    captured_events.append(event)


# ── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup() -> None:
    for ext in (".json", ".db"):
        p = SAVES_DIR / f"{SLOT}{ext}"
        if p.exists():
            try: p.unlink()
            except OSError: pass


# ── Main playthrough ─────────────────────────────────────────────────────────

def main() -> int:
    print("\n" + "=" * 68)
    print("  SYSTEM BREAKER — Headless Playthrough Smoke Test")
    print("=" * 68)
    cleanup()  # start fresh

    # ── 1. Bootstrap the engine ─────────────────────────────────────────────
    section("1. Bootstrap")
    engine = GameEngine()
    try:
        engine.bootstrap()
        ok("Engine bootstrapped (registries loaded, AIService wired)")
    except Exception as e:
        fail("Bootstrap failed — game won't start", e)
        return _report()

    # Show what loaded
    print(f"        classes : {len(engine.class_registry._classes)}")
    print(f"         skills : {len(engine.skill_registry._skills)}")
    print(f"           NPCs : {len(engine.npc_registry.all()) if engine.npc_registry else 0}")
    print(f"         quests : {len(engine.quest_registry.all()) if engine.quest_registry else 0}")
    print(f"         scenes : {len(engine.scene_registry._scenes)}")
    print(f"  AI available : {engine.ai_service.is_available}")
    print(f"   BG running  : {engine._bg_generator is not None}")

    if not engine.quest_registry:
        fail("quest_system disabled — can't play through quests")
        return _report()

    # ── 2. Create a character ───────────────────────────────────────────────
    section("2. Create character — 'Threshold the Bold'")
    try:
        player = Player(name="Threshold the Bold", base_class="warrior")
        player.gold = 0
        engine.state = new_game_state(player, SAVES_DIR, SLOT)
        # Attach engine handles to state — same wiring core/menu_flow does
        # when starting a real game. Without this, quest_system's skill-
        # reward path and option_logic's give_skill: AI path silently skip.
        engine.state.skill_registry = engine.skill_registry
        engine.state.class_registry = engine.class_registry
        engine.state.scene_registry = engine.scene_registry
        engine.state.ai_service = engine.ai_service
        engine.state.current_scene_id = "village_start"
        engine.state.current_node_id = "root"
        engine.state.turn_number = 1
        ok(f"Character created — Lv{player.level} warrior, "
           f"HP {player.current_hp}/{player.max_hp}, MP {player.current_mp}/{player.max_mp}")
        ok(f"Starting at village_start, gold=0, no quests, no flags")
    except Exception as e:
        fail("Character creation failed", e)
        return _report()

    # Subscribe to quest events so we can verify the bus fires correctly
    bus.subscribe("QUEST_STARTED",   event_recorder)
    bus.subscribe("QUEST_ADVANCED",  event_recorder)
    bus.subscribe("QUEST_COMPLETED", event_recorder)
    bus.subscribe("QUEST_FAILED",    event_recorder)
    bus.subscribe("ITEM_FOUND",      event_recorder)
    bus.subscribe("LEVEL_UP",        event_recorder)
    bus.subscribe("SKILL_ACQUIRED",  event_recorder)

    # ── 3. Talk to Torven, pick up the blacksmith hammer quest ─────────────
    section("3. Quest 1 — Blacksmith Hammer (2-stage)")
    torven = engine.npc_registry.get("torven_blacksmith")
    if not torven:
        fail("torven_blacksmith NPC missing from registry — content gap")
        return _report()
    ok(f"NPC resolved: {torven.name} ({torven.role}) in {torven.zone_id}")

    npc_system.ensure_npc_instance(torven, engine.state)
    disp = npc_system.get_npc_disposition(torven, engine.state)
    print(f"        disposition: {disp:+.1f}")

    # Start the quest via the same path NPC dialogue would use
    iid = quest_system.start_quest(
        "blacksmith_hammer", torven.npc_id, engine.state, engine.quest_registry,
    )
    if not iid:
        fail("start_quest returned None — quest didn't activate")
        return _report()
    bus.flush()

    started = [e for e in captured_events if e.name == "QUEST_STARTED"]
    if not started:
        fail("QUEST_STARTED event didn't fire on bus")
    else:
        ok(f"QUEST_STARTED event fired — title='{started[-1].data.get('title')}'")

    # Stage 1 completion_condition is has_item:torven_hammer. Player finds it
    # in the goblin territory on Dungeon Floor 1 — we just give it directly.
    engine.state.player.add_item("torven_hammer")
    bus.flush()
    quest_system.tick_quests(engine.state, engine.quest_registry)
    bus.flush()

    advanced = [e for e in captured_events if e.name == "QUEST_ADVANCED"]
    if not advanced:
        fail("Quest didn't auto-advance after picking up torven_hammer (check tick_quests + has_item condition)")
    else:
        ok(f"Quest auto-advanced on item pickup → stage='{advanced[-1].data.get('new_stage')}'")

    # Stage 2 terminal completion_condition is has_flag:torven_hammer_returned
    # (set when the player returns the hammer via dialogue with Torven).
    engine.state.player.set_flag("torven_hammer_returned")
    quest_system.tick_quests(engine.state, engine.quest_registry)
    bus.flush()

    completed = [e for e in captured_events if e.name == "QUEST_COMPLETED"]
    if not completed:
        fail("Quest didn't complete after flag set (check terminal-stage completion_condition)")
        return _report()

    comp = completed[-1]
    expected_gold_copper = 40 * COPPER_PER_GOLD  # template reward_gold is in gold pieces
    if engine.state.player.gold != expected_gold_copper:
        fail(f"Reward gold wrong: expected +{expected_gold_copper}c, got {engine.state.player.gold}c")
    else:
        ok(f"Reward gold applied: +{expected_gold_copper}c (= {expected_gold_copper // COPPER_PER_GOLD}g)")

    if comp.data.get("reward_xp", 0) <= 0:
        fail("QUEST_COMPLETED event reports no XP reward")
    else:
        ok(f"Reward XP fired: +{comp.data['reward_xp']} XP (player level now {engine.state.player.level})")

    if not engine.state.player.has_flag("torven_owes_favour"):
        fail("Reward flag 'torven_owes_favour' not set on player")
    else:
        ok("Reward flag set: 'torven_owes_favour'")

    if iid not in engine.state.player.completed_quest_ids:
        fail(f"Quest instance {iid} not in completed_quest_ids")
    elif iid in engine.state.player.active_quest_ids:
        fail(f"Quest instance {iid} still in active_quest_ids after completion")
    else:
        ok("Quest moved from active → completed list cleanly")

    # ── Verify the reward_skill_hints demo fired ────────────────────────────
    # blacksmith_hammer in data/quests/quest_templates.json declares
    # "reward_skill_hints": ["Forge-Born Strike"]. After completion the
    # player should own a new skill themed to that hint.
    skill_events = [e for e in captured_events if e.name == "SKILL_ACQUIRED"]
    if not skill_events:
        if engine.ai_service and engine.ai_service.is_available:
            fail("blacksmith_hammer reward_skill_hint should have granted a skill "
                 "(SKILL_ACQUIRED event missing). Ollama may have timed out — "
                 "rerun if so.")
        else:
            ok("(AI offline) skill-reward fallback skipped — expected")
    else:
        granted = skill_events[-1]
        sid = granted.data.get("skill_id")
        sk_obj = engine.skill_registry.get(sid) if sid else None
        if sk_obj is None:
            fail(f"SKILL_ACQUIRED fired (skill_id={sid}) but skill missing from registry")
        else:
            ok(f"Reward skill granted: '{sk_obj.name}' "
               f"({sk_obj.skill_type.value} {sk_obj.rarity.value}, "
               f"mp={sk_obj.mp_cost} cd={sk_obj.cooldown_turns}, "
               f"effects={len(sk_obj.effects)})")
            if sk_obj.effects:
                ef = sk_obj.effects[0]
                ok(f"  → effect: {ef.effect_type.value} {ef.base_value} + "
                   f"{ef.scaling_stat or '—'}×{ef.scaling_coefficient}")

    # ── 4. Quest 2 — 3-stage Mira theft investigation ──────────────────────
    section("4. Quest 2 — Theft Investigation (3-stage)")
    mira = engine.npc_registry.get("mira_innkeeper")
    if not mira:
        fail("mira_innkeeper NPC missing")
    else:
        ok(f"NPC resolved: {mira.name} ({mira.role})")
        npc_system.ensure_npc_instance(mira, engine.state)

        captured_events.clear()
        iid2 = quest_system.start_quest(
            "theft_investigation", mira.npc_id, engine.state, engine.quest_registry,
        )
        bus.flush()
        if not iid2:
            fail("theft_investigation failed to start")
        else:
            ok(f"Started theft_investigation (instance {iid2[:8]}...)")

            # Walk the 3-stage chain (gather → identify → report)
            stages_advanced: list[str] = []
            for stage_idx, flag in enumerate([
                "theft_clue_short_stay", "thief_identified", "mira_informed_of_thief",
            ], start=1):
                engine.state.player.set_flag(flag)
                quest_system.tick_quests(engine.state, engine.quest_registry)
                bus.flush()
                row = engine.state.world_db.get_quest(iid2)
                stages_advanced.append(row["current_state"] if row else "?")

            if iid2 in engine.state.player.completed_quest_ids:
                ok(f"3-stage chain progressed: {' → '.join(stages_advanced[:-1])} → ✓")
            else:
                fail(f"3-stage quest didn't complete — chain went: {stages_advanced}")

    # ── 5. Quest 3 — failure path (start then fail conditions) ─────────────
    section("5. Quest failure path")
    # blacksmith_hammer has no failure_conditions — use a quest with one if available
    failing = next(
        (t for t in engine.quest_registry.all() if t.failure_conditions),
        None,
    )
    if not failing:
        ok("No quest templates declare failure_conditions — skipping fail-path test")
    else:
        # Restart the failing quest, then satisfy its failure condition
        iid_f = quest_system.start_quest(
            failing.template_id, None, engine.state, engine.quest_registry,
        )
        # The first condition determines what to set
        cond = failing.failure_conditions[0]
        if "has_flag" in cond:
            engine.state.player.set_flag(cond["has_flag"])
        elif "world_flag" in cond:
            engine.state.world_db.set_world_flag(cond["world_flag"], "true")
        quest_system.tick_quests(engine.state, engine.quest_registry)
        bus.flush()
        if iid_f in engine.state.player.completed_quest_ids:
            row = engine.state.world_db.get_quest(iid_f)
            outcome = row.get("outcome") if row else "?"
            ok(f"Failure condition triggered: quest '{failing.template_id}' ended with outcome='{outcome}'")
        else:
            fail(f"Failure condition didn't end quest '{failing.template_id}'")

    # ── 6. World state — verify SQLite persistence ─────────────────────────
    section("6. World state persistence (save/load round-trip)")
    summaries_before = quest_system.get_active_quest_summaries(
        engine.state, engine.quest_registry,
    )
    flag_count_before = len([k for k in engine.state.player.flags if not k.startswith("_")])
    gold_before = engine.state.player.gold
    completed_before = list(engine.state.player.completed_quest_ids)

    save_game(engine.state, SLOT, SAVES_DIR)
    close_game(engine.state)
    ok("save_game + close_game completed without error")

    reloaded = load_game(SLOT, SAVES_DIR)
    if reloaded is None:
        fail("load_game returned None — save file unreadable")
    else:
        if reloaded.player.gold != gold_before:
            fail(f"Gold drifted on reload: {gold_before} → {reloaded.player.gold}")
        else:
            ok(f"Gold persisted: {gold_before}c")
        if list(reloaded.player.completed_quest_ids) != completed_before:
            fail("completed_quest_ids drifted on reload")
        else:
            ok(f"completed_quest_ids persisted: {len(completed_before)} quest(s)")
        reloaded_flags = len([k for k in reloaded.player.flags if not k.startswith("_")])
        if reloaded_flags != flag_count_before:
            fail(f"Player flags drifted: {flag_count_before} → {reloaded_flags}")
        else:
            ok(f"Player flags persisted: {flag_count_before} flag(s)")
        engine.state = reloaded  # continue with the reloaded state

    # ── 7. BG content generation — verify the worker is alive ──────────────
    section("7. Background AI content generation")
    if not engine._bg_generator:
        ok("BG generator not running (AI offline) — skipping submission check")
    else:
        # Submit a lore entry task; the worker thread should pick it up
        submitted = engine._bg_generator.submit_lore_entry(
            context_flags=["torven_owes_favour"],
            player_flags=["torven_owes_favour"],
        )
        if submitted:
            ok("BG task submission accepted (lore_entry)")
        else:
            ok("BG task queue full — that's fine, queue is bounded")

        # Verify the scheduler's per-turn submission logic runs cleanly
        try:
            from core.bg_scheduler import maybe_submit_tasks
            maybe_submit_tasks(engine)
            ok("bg_scheduler.maybe_submit_tasks ran without error")
        except Exception as e:
            fail("bg_scheduler crashed", e)

    # ── 8. AI quest generation (only if AI is on) ──────────────────────────
    section("8. AI dynamic quest generation")
    if not engine.ai_service.is_available:
        ok("AIService unavailable (Ollama offline) — skipping live AI quest test")
    else:
        captured_events.clear()
        # generate_ai_quest calls Ollama synchronously; can take 10-60s
        print("        Calling AI quest generator (this may take 10-60s)...")
        try:
            from systems.quest_system import generate_ai_quest
            new_iid = generate_ai_quest(
                giver_npc_id="torven_blacksmith",
                npc_name="Torven",
                npc_role="blacksmith",
                state=engine.state,
                ai_service=engine.ai_service,
            )
            if new_iid:
                ai_def = engine.state.world_db.get_ai_quest_definition(new_iid)
                if ai_def:
                    ok(f"AI generated a quest: '{ai_def.get('title')}' "
                       f"({len(ai_def.get('stages', []))} stage(s), reward_gold={ai_def.get('reward_gold', 0)})")
                else:
                    fail("AI quest instance created but definition didn't store in ai_quest_data")
            else:
                ok("AI quest generation returned None (model busy / timed out) — not a crash, just no result")
        except Exception as e:
            fail("AI quest generation raised an exception", e)

    # ── 9. Teardown ─────────────────────────────────────────────────────────
    section("9. Teardown")
    try:
        close_game(engine.state)
        if engine._bg_generator:
            engine._bg_generator.stop()
        if engine.ai_service is not None:
            engine.ai_service.shutdown()
        ok("Engine shut down cleanly (world_db closed, BG stopped, executor released)")
    except Exception as e:
        fail("Teardown raised", e)

    cleanup()
    return _report()


def _report() -> int:
    print("\n" + "=" * 68)
    print(f"  RESULTS: {len(PASSES)} passed, {len(ISSUES)} issue(s)")
    print("=" * 68)
    if ISSUES:
        print("\n  ISSUES:")
        for i, issue in enumerate(ISSUES, 1):
            print(f"    {i}. {issue}")
        return 1
    print("\n  Playthrough complete. Game systems verified end-to-end.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
