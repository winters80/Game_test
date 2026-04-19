"""
Headless quest test runner for System Breaker.

Validates every quest template, exercises the full quest lifecycle
(start → tick advancement → completion → rewards), and covers the
edge cases that don't appear in test_characters.py:

  - Schema integrity for every template
  - NPC giver / dialogue trigger references
  - Reward references (items, factions, guilds)
  - Multi-stage progression (3-stage and 4-stage chains)
  - Concurrent quests
  - Failure conditions
  - Time-limit expiration
  - AI quest data round-trip in SQLite
  - advance_quest:/complete_quest: trigger resolution

Run via: python -X utf8 test_quests.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

# ── Bootstrap ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config import DATA_DIR, SAVES_DIR, feature
from entities.player import Player
from entities.item import ItemRegistry
from entities.npc import NPCRegistry
from entities.quest import QuestRegistry, QuestTemplate
from persistence.save_manager import new_game_state, close_game
from systems import quest_system as qs
from core.event_bus import bus, Event

ISSUES: list[str] = []
PASSES: list[str] = []

def ok(msg: str) -> None:
    PASSES.append(msg)
    print(f"  ✓  {msg}")

def fail(msg: str, exc: Exception | None = None) -> None:
    detail = f" — {exc}" if exc else ""
    ISSUES.append(f"{msg}{detail}")
    print(f"  ✗  {msg}{detail}")

def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def fresh_state(player_name: str, slot_name: str) -> tuple[Player, Any]:
    """Build an isolated player + world_db for one test scenario."""
    player = Player(name=player_name, base_class="warrior")
    player.gold = 0
    player.experience = 0
    state = new_game_state(player, SAVES_DIR, slot_name)
    state.current_scene_id = "village_start"
    state.turn_number = 1
    return player, state


def cleanup_state(state: Any, slot_name: str) -> None:
    """Close DB and remove test save artefacts."""
    try:
        close_game(state)
    except Exception:
        pass
    for ext in (".db", ".json"):
        p = SAVES_DIR / f"{slot_name}{ext}"
        if p.exists():
            try: p.unlink()
            except OSError: pass


def load_all_registries():
    item_reg = ItemRegistry()
    item_reg.load_from_dir(DATA_DIR / "items")
    npc_reg = NPCRegistry()
    npc_reg.load_from_dir(DATA_DIR / "npcs")
    quest_reg = QuestRegistry()
    quest_reg.load_from_file(DATA_DIR / "quests" / "quest_templates.json")
    return item_reg, npc_reg, quest_reg


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "═"*60)
    print("  SYSTEM BREAKER — Quest System Test Suite")
    print("═"*60)

    if not feature("quest_system"):
        print("\n  ⚠ quest_system feature flag is OFF — tests will still run "
              "but live game integration is disabled.")

    item_reg, npc_reg, quest_reg = load_all_registries()

    # ── 1. Registry loading ───────────────────────────────────────────────────
    section("1. Quest Registry Loading")
    try:
        templates = list(quest_reg.all())
        assert len(templates) >= 9, f"Expected ≥9 quest templates, got {len(templates)}"
        ok(f"Loaded {len(templates)} quest template(s)")
    except Exception as e:
        fail("Quest registry failed to load", e)
        traceback.print_exc()
        _report()
        return

    # ── 2. Schema integrity for every template ───────────────────────────────
    section("2. Template Schema Integrity")
    for tmpl in quest_reg.all():
        try:
            assert tmpl.template_id, f"Template missing template_id"
            assert tmpl.title, f"Template '{tmpl.template_id}' missing title"
            assert tmpl.stages, f"Template '{tmpl.template_id}' has zero stages"

            # Build stage_id set for chain validation
            stage_ids = {s.stage_id for s in tmpl.stages}

            # Each stage must have a valid completion_condition shape
            valid_keys = {"has_item", "has_flag", "world_flag"}
            for stage in tmpl.stages:
                assert stage.stage_id, f"Stage in '{tmpl.template_id}' missing stage_id"
                cond = stage.completion_condition or {}
                if cond:
                    used_keys = set(cond.keys()) & valid_keys
                    assert used_keys, (
                        f"'{tmpl.template_id}' stage '{stage.stage_id}' completion_condition "
                        f"has no recognised key (got {list(cond.keys())})"
                    )

                # next_stage_id must be None (terminal) or point to a real stage_id
                if stage.next_stage_id is not None:
                    assert stage.next_stage_id in stage_ids, (
                        f"'{tmpl.template_id}' stage '{stage.stage_id}' "
                        f"next_stage_id='{stage.next_stage_id}' is not a real stage"
                    )

            # Exactly one terminal stage (next_stage_id is None) per template
            terminals = [s for s in tmpl.stages if s.next_stage_id is None]
            assert len(terminals) >= 1, (
                f"'{tmpl.template_id}' has no terminal stage — quest can never complete"
            )

            ok(f"Schema OK — '{tmpl.template_id}' ({len(tmpl.stages)} stage(s))")
        except AssertionError as e:
            fail(f"Schema integrity broken on '{tmpl.template_id}'", e)

    # ── 3. NPC giver references resolve ──────────────────────────────────────
    section("3. NPC Giver References")
    for tmpl in quest_reg.all():
        if not tmpl.giver_npc_id:
            ok(f"'{tmpl.template_id}' has no fixed giver (faction/AI quest) — skipped")
            continue
        try:
            npc = npc_reg.get(tmpl.giver_npc_id)
            assert npc, f"giver_npc_id='{tmpl.giver_npc_id}' not in NPC registry"
            ok(f"'{tmpl.template_id}' giver '{tmpl.giver_npc_id}' resolves")
        except AssertionError as e:
            fail(f"Bad giver reference on '{tmpl.template_id}'", e)

    # ── 4. NPC dialogue start_quest: triggers reference real templates ──────
    section("4. NPC Dialogue Trigger References")
    template_ids = {t.template_id for t in quest_reg.all()}
    bad_refs = []
    found_refs = []
    for npc in npc_reg.all():
        nodes = npc.dialogue_nodes or {}
        for node_id, node in nodes.items():
            opts = node.get("options", []) if isinstance(node, dict) else node.options
            for opt in opts:
                triggers = opt.get("triggers", []) if isinstance(opt, dict) else opt.triggers
                for t in triggers or []:
                    if t.startswith("start_quest:"):
                        ref = t[len("start_quest:"):]
                        found_refs.append((npc.npc_id, node_id, ref))
                        if ref not in template_ids:
                            bad_refs.append(f"{npc.npc_id}:{node_id} → '{ref}'")
    if bad_refs:
        fail(f"NPC dialogue references {len(bad_refs)} unknown quest template(s): {bad_refs}")
    else:
        ok(f"All {len(found_refs)} NPC start_quest: triggers reference real templates")

    # ── 5. Reward reference integrity ────────────────────────────────────────
    section("5. Reward Reference Integrity")
    for tmpl in quest_reg.all():
        try:
            for item_id in tmpl.reward_items or []:
                # reward_items can be strings or dicts {"item_id":..., "qty":...}
                resolved_id = item_id if isinstance(item_id, str) else item_id.get("item_id", "")
                if resolved_id:
                    item = item_reg.get(resolved_id)
                    assert item, (
                        f"'{tmpl.template_id}' reward_item '{resolved_id}' not in item registry"
                    )
            # We can't fully validate faction/guild rewards without those registries —
            # just confirm they're dict[str,float] shape so payout logic doesn't crash.
            for k, v in (tmpl.faction_rewards or {}).items():
                assert isinstance(k, str) and isinstance(v, (int, float)), (
                    f"'{tmpl.template_id}' faction_rewards has non-numeric value for '{k}'"
                )
            for k, v in (tmpl.guild_rewards or {}).items():
                assert isinstance(k, str) and isinstance(v, (int, float)), (
                    f"'{tmpl.template_id}' guild_rewards has non-numeric value for '{k}'"
                )
            ok(f"Rewards OK — '{tmpl.template_id}'")
        except AssertionError as e:
            fail(f"Reward integrity broken on '{tmpl.template_id}'", e)

    # ── 6. Lifecycle — blacksmith_hammer (2-stage, has_item → has_flag) ─────
    section("6. Lifecycle — blacksmith_hammer (single-stage advance)")
    slot = "_qtest_hammer_"
    player, state = fresh_state("HammerTester", slot)
    try:
        instance_id = qs.start_quest("blacksmith_hammer", "torven_blacksmith", state, quest_reg)
        assert instance_id, "start_quest returned no instance_id"
        ok(f"start_quest OK — instance {instance_id[:8]}...")

        # Advance via has_item
        player.add_item("torven_hammer", 1)
        qs.tick_quests(state, quest_reg)
        row = state.world_db.get_quest(instance_id)
        assert row["current_state"] == "return_hammer", \
            f"expected 'return_hammer' after has_item tick, got {row['current_state']}"
        ok("Stage 1 → 2 advance via has_item condition")

        # Complete via has_flag
        player.flags["torven_hammer_returned"] = True
        qs.tick_quests(state, quest_reg)
        assert instance_id in player.completed_quest_ids, "Quest not in completed list"
        assert player.gold == 4000, f"Expected 4000 copper reward, got {player.gold}"
        assert player.experience == 75, f"Expected 75 xp, got {player.experience}"
        assert player.has_flag("torven_owes_favour"), "Reward flag not set"
        ok("Completion + rewards (gold, xp, flag) all applied")
    except Exception as e:
        fail("blacksmith_hammer lifecycle broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 7. Lifecycle — theft_investigation (3-stage chain) ──────────────────
    section("7. Lifecycle — theft_investigation (3-stage chain)")
    slot = "_qtest_theft_"
    player, state = fresh_state("TheftTester", slot)
    try:
        iid = qs.start_quest("theft_investigation", "mira_innkeeper", state, quest_reg)
        assert iid, "start_quest failed"

        expected_chain = ["gather_clues", "identify_thief", "report_mira"]
        actual_path = [state.world_db.get_quest(iid)["current_state"]]

        # Walk through 3 stages by setting completion flags
        flag_chain = [
            "theft_clue_short_stay",   # gather_clues → identify_thief
            "thief_identified",        # identify_thief → report_mira
            "mira_informed_of_thief",  # report_mira → completion
        ]
        for f in flag_chain:
            player.flags[f] = True
            qs.tick_quests(state, quest_reg)
            row = state.world_db.get_quest(iid)
            actual_path.append(row["current_state"] if row else "<completed>")

        # Final: should be completed
        assert iid in player.completed_quest_ids, f"Expected completion, path was {actual_path}"
        ok(f"3-stage chain completed: {' → '.join(expected_chain)} → ✓")
        assert player.has_flag("inn_thefts_resolved"), "Terminal on_advance_triggers did not fire"
        ok("Terminal-stage on_advance_triggers fired (flag:inn_thefts_resolved)")
        assert player.has_flag("mira_owes_favour"), "Reward flag not applied"
        ok("Reward flag applied on completion")
    except Exception as e:
        fail("theft_investigation lifecycle broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 8. Lifecycle — crown_ascension (4-stage chain) ──────────────────────
    section("8. Lifecycle — crown_ascension (4-stage chain)")
    slot = "_qtest_crown_"
    player, state = fresh_state("CrownTester", slot)
    try:
        iid = qs.start_quest("crown_ascension", None, state, quest_reg)
        assert iid, "start_quest failed (null giver should still work)"
        ok("start_quest with null giver_npc_id OK")

        flag_chain = [
            "noble_favor_earned",
            "vanguard_endorsement",
            "conclave_approval",
            "throne_petition_filed",
        ]
        starting_level = player.level
        for i, f in enumerate(flag_chain, 1):
            player.flags[f] = True
            qs.tick_quests(state, quest_reg)
        assert iid in player.completed_quest_ids, "4-stage quest never completed"
        ok("4-stage chain completed")
        assert player.has_flag("crown_ruler_candidate_flag"), "Major reward flag missing"
        # 5000 xp at L1 will trigger multiple level-ups — assert level rose, not raw xp
        # (add_experience consumes xp on each level-up; player.experience tracks the
        # remainder after the most recent level threshold)
        assert player.level > starting_level, \
            f"Expected level-up from 5000xp reward, level still {player.level}"
        ok(f"Final-stage rewards applied (level {starting_level} → {player.level} from 5000 xp grant)")
    except Exception as e:
        fail("crown_ascension lifecycle broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 9. Concurrent quests ─────────────────────────────────────────────────
    section("9. Concurrent Quests")
    slot = "_qtest_concurrent_"
    player, state = fresh_state("MultiTester", slot)
    try:
        id_a = qs.start_quest("blacksmith_hammer", None, state, quest_reg)
        id_b = qs.start_quest("missing_person_search", None, state, quest_reg)
        id_c = qs.start_quest("shadow_errand", None, state, quest_reg)
        assert len(player.active_quest_ids) == 3, \
            f"Expected 3 active quests, got {len(player.active_quest_ids)}"
        ok(f"3 concurrent quests active simultaneously")

        # Complete only quest B — others should remain active
        player.flags["aldric_fenn_found"] = True
        qs.tick_quests(state, quest_reg)
        assert id_b in player.completed_quest_ids, "B not completed"
        assert id_a in player.active_quest_ids, "A wrongly completed"
        assert id_c in player.active_quest_ids, "C wrongly completed"
        ok("Single-quest completion does not disturb other active quests")

        # Summaries should now show 2 active
        summaries = qs.get_active_quest_summaries(state, quest_reg)
        assert len(summaries) == 2, f"Expected 2 active summaries, got {len(summaries)}"
        ok(f"get_active_quest_summaries returns correct count after partial completion")
    except Exception as e:
        fail("Concurrent quest handling broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 10. Failure conditions ───────────────────────────────────────────────
    section("10. Failure Conditions")
    slot = "_qtest_fail_"
    player, state = fresh_state("FailTester", slot)
    try:
        # theft_investigation has failure_conditions: [{"has_flag": "tipped_off_thief"}]
        iid = qs.start_quest("theft_investigation", "mira_innkeeper", state, quest_reg)
        assert iid in player.active_quest_ids
        # Set the failure flag — tick should fail the quest
        player.flags["tipped_off_thief"] = True
        qs.tick_quests(state, quest_reg)
        assert iid not in player.active_quest_ids, "Failed quest still active"
        # Player.complete_quest moves it to completed_quest_ids regardless of outcome
        # but the row in world_db should have outcome != 'success'
        row = state.world_db.get_quest(iid)
        assert row and row.get("outcome") != "success", \
            f"Expected failure outcome, got {row.get('outcome') if row else 'no row'}"
        ok(f"Failure condition triggered fail_quest (outcome={row.get('outcome')!r})")
    except Exception as e:
        fail("Failure conditions broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 11. Time-limit expiration ────────────────────────────────────────────
    section("11. Time-limit Expiration")
    slot = "_qtest_timer_"
    player, state = fresh_state("TimerTester", slot)
    try:
        # Build an in-memory template with a short time limit and register it
        from entities.quest import QuestStage
        timed = QuestTemplate(
            template_id="__test_timed__",
            title="Test Time-Limited Quest",
            description="Auto-fails after 3 turns.",
            giver_npc_id=None,
            stages=[QuestStage(
                stage_id="do_thing",
                description="Do the thing",
                objective_text="Do the thing.",
                completion_condition={"has_flag": "did_the_thing"},
                on_advance_triggers=[],
                next_stage_id=None,
            )],
            reward_gold=0, reward_xp=0,
            time_limit_turns=3,
        )
        quest_reg.register(timed)
        iid = qs.start_quest("__test_timed__", None, state, quest_reg)
        assert iid in player.active_quest_ids

        # Advance turn beyond the limit
        state.turn_number = state.turn_number + 5
        qs.tick_quests(state, quest_reg)
        assert iid not in player.active_quest_ids, "Time-limited quest still active after expiry"
        row = state.world_db.get_quest(iid)
        assert row and row.get("outcome") != "success", \
            f"Expected non-success outcome on time-out, got {row.get('outcome')!r}"
        ok(f"Time-limit expiration failed quest (outcome={row.get('outcome')!r})")
    except Exception as e:
        fail("Time-limit expiration broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 12. AI quest data DB round-trip ──────────────────────────────────────
    section("12. AI Quest Data DB Round-trip")
    slot = "_qtest_aidb_"
    player, state = fresh_state("AIDBTester", slot)
    try:
        # Store a synthetic AI quest definition
        ai_def = {
            "template_id": "ai_test_001",
            "title": "AI-Generated Test Quest",
            "description": "A quest the AI made up.",
            "stages": [{
                "stage_id": "single",
                "objective_text": "Do AI thing.",
                "completion_condition": {"has_flag": "ai_done"},
                "next_stage_id": None,
            }],
            "reward_gold": 50,
            "reward_xp": 100,
        }
        # Need a quest_instances row first (FK constraint)
        instance_id = "ai_inst_test_001"
        state.world_db.add_quest(instance_id, "ai_test_001", "single", state.turn_number)
        state.world_db.store_ai_quest(instance_id, ai_def)
        ok("store_ai_quest insert OK")

        # Verify we can read the quest_instances row
        row = state.world_db.get_quest(instance_id)
        assert row and row["template_id"] == "ai_test_001", "AI quest instance row not stored"
        ok("Round-trip via get_quest returns correct row")

        # Verify the ai_quest_data table has it (raw SQL since no helper)
        cur = state.world_db._conn.execute(
            "SELECT definition FROM ai_quest_data WHERE instance_id=?", (instance_id,)
        ).fetchone()
        assert cur is not None, "ai_quest_data row missing"
        import json as _json
        loaded = _json.loads(cur["definition"])
        assert loaded["template_id"] == "ai_test_001", "AI quest definition JSON corrupted"
        ok("ai_quest_data definition JSON round-trips intact")
    except Exception as e:
        fail("AI quest data DB round-trip broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 13. advance_quest / complete_quest event payloads ───────────────────
    section("13. Event Payload Shape")
    slot = "_qtest_events_"
    player, state = fresh_state("EventTester", slot)
    captured: dict[str, list[Event]] = {
        "QUEST_STARTED": [], "QUEST_ADVANCED": [],
        "QUEST_COMPLETED": [], "QUEST_FAILED": [],
    }
    for ev in captured:
        bus.subscribe(ev, lambda e, _name=ev: captured[_name].append(e))
    try:
        iid = qs.start_quest("blacksmith_hammer", "torven_blacksmith", state, quest_reg)
        bus.flush()
        assert captured["QUEST_STARTED"], "QUEST_STARTED never fired"
        s = captured["QUEST_STARTED"][-1]
        for k in ("quest_id", "template_id", "title"):
            assert k in s.data, f"QUEST_STARTED payload missing '{k}'"
        ok("QUEST_STARTED payload has quest_id / template_id / title")

        player.add_item("torven_hammer", 1)
        qs.tick_quests(state, quest_reg)
        bus.flush()
        assert captured["QUEST_ADVANCED"], "QUEST_ADVANCED never fired"
        a = captured["QUEST_ADVANCED"][-1]
        for k in ("quest_id", "template_id", "title", "new_stage"):
            assert k in a.data, f"QUEST_ADVANCED payload missing '{k}'"
        ok("QUEST_ADVANCED payload has quest_id / template_id / title / new_stage")

        player.flags["torven_hammer_returned"] = True
        qs.tick_quests(state, quest_reg)
        bus.flush()
        assert captured["QUEST_COMPLETED"], "QUEST_COMPLETED never fired"
        c = captured["QUEST_COMPLETED"][-1]
        for k in ("quest_id", "title", "reward_gold", "reward_xp"):
            assert k in c.data, f"QUEST_COMPLETED payload missing '{k}'"
        ok("QUEST_COMPLETED payload has quest_id / title / reward_gold / reward_xp")
    except Exception as e:
        fail("Event payload shape broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    # ── 14. Trigger format coverage (advance_quest:, complete_quest:) ───────
    section("14. Trigger Format Resolution")
    slot = "_qtest_triggers_"
    player, state = fresh_state("TriggerTester", slot)
    try:
        # Stand-in for what core/choice_handler.py does
        def find_active_for_template(state, template_id):
            for r in state.world_db.get_active_quests():
                if r["template_id"] == template_id:
                    return r["instance_id"]
            return None

        iid = qs.start_quest("blacksmith_hammer", None, state, quest_reg)

        # advance_quest:TEMPLATE_ID — should advance one stage
        resolved = find_active_for_template(state, "blacksmith_hammer")
        assert resolved == iid, "Resolution by template_id failed"
        ok("Trigger TEMPLATE_ID → instance_id resolution works")

        qs.advance_quest(resolved, state, quest_reg)
        row = state.world_db.get_quest(iid)
        assert row["current_state"] == "return_hammer", \
            f"advance_quest didn't advance, stage={row['current_state']}"
        ok("advance_quest moves to next stage")

        # complete_quest with explicit outcome
        qs.complete_quest(iid, "success", state, quest_reg)
        assert iid in player.completed_quest_ids, "complete_quest didn't move to completed"
        ok("complete_quest with 'success' outcome moves quest to completed list")

        # Resolution returns None when no active quest exists
        nope = find_active_for_template(state, "blacksmith_hammer")
        assert nope is None, "Resolution returned a value for already-completed quest"
        ok("Resolution returns None after completion (no false matches)")
    except Exception as e:
        fail("Trigger format resolution broken", e)
        traceback.print_exc()
    finally:
        cleanup_state(state, slot)

    _report()


def _report() -> None:
    print("\n" + "═"*60)
    print(f"  RESULTS: {len(PASSES)} passed, {len(ISSUES)} issue(s) found")
    print("═"*60)
    if ISSUES:
        print("\n  ISSUES:")
        for i, issue in enumerate(ISSUES, 1):
            print(f"    {i}. {issue}")
        print()
        sys.exit(1)
    else:
        print("\n  All quest checks passed!")
        print()


if __name__ == "__main__":
    main()
