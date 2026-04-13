"""
Test guild betrayal, coup, and splinter adjudication.

Run with: python tests/test_guild_betrayal.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
results: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    results.append((label, condition, detail))
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail and not condition else ""
    print(f"  {status} {label}{suffix}")
    return condition


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


section("Run 1 — Betrayal risk calculation")
try:
    from systems.guilds.guild_engine import compute_betrayal_risk
    from systems.guilds.guild_models import GuildState, GuildMember

    guild_unstable = GuildState(
        guild_id="g1", template_id="t1", name="Test", founding_turn=0,
        headquarters_zone_id="zone", archetype="combat", stability=30,
    )
    guild_stable = GuildState(
        guild_id="g2", template_id="t1", name="Test", founding_turn=0,
        headquarters_zone_id="zone", archetype="combat", stability=70,
    )

    high_risk_member = GuildMember(
        guild_id="g1", entity_id="npc_x", entity_type="npc",
        rank_id="soldier", joined_turn=0, loyalty=20, ambition=80,
    )
    low_risk_member = GuildMember(
        guild_id="g2", entity_id="npc_y", entity_type="npc",
        rank_id="soldier", joined_turn=0, loyalty=80, ambition=20,
    )

    high_risk = compute_betrayal_risk(high_risk_member, guild_unstable)
    low_risk = compute_betrayal_risk(low_risk_member, guild_stable)

    check("High-risk member risk > 60", high_risk > 60, f"got {high_risk:.1f}")
    check("Low-risk member risk < 30", low_risk < 30, f"got {low_risk:.1f}")
    check("Risk clamped to [0,100]", 0 <= high_risk <= 100 and 0 <= low_risk <= 100)
except Exception:
    print(f"  {FAIL} Run 1 crashed:\n{traceback.format_exc()}")


section("Run 2 — Eligible actions unlock at thresholds")
try:
    from systems.guilds.guild_engine import get_eligible_actions

    actions_55  = get_eligible_actions(55)
    actions_60  = get_eligible_actions(60)
    actions_75  = get_eligible_actions(75)
    actions_85  = get_eligible_actions(85)

    check("risk=55 → no eligible actions", len(actions_55) == 0, str(actions_55))
    check("risk=60 → includes steal_resources", "steal_resources" in actions_60, str(actions_60))
    check("risk=60 → includes leak_secrets", "leak_secrets" in actions_60, str(actions_60))
    check("risk=75 → includes attempt_coup", "attempt_coup" in actions_75, str(actions_75))
    check("risk=85 → includes found_splinter_guild", "found_splinter_guild" in actions_85, str(actions_85))
except Exception:
    print(f"  {FAIL} Run 2 crashed:\n{traceback.format_exc()}")


section("Run 3 — Coup adjudication")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_engine import adjudicate_intent
    from systems.guilds.guild_models import AttemptCoupIntent, GuildState, GuildMember
    from systems.guilds import guild_repo

    db = WorldDatabase(":memory:")
    db.open()

    # Build a guild with 3 disloyal supporters + 1 defender, low morale
    db.add_guild_state(
        guild_id="g_coup", template_id="t1", name="Iron Fist",
        founding_turn=0, headquarters_zone_id="zone", archetype="combat",
    )
    db.update_guild_state("g_coup", morale=30, stability=40, current_leader_id="npc_old_leader")
    for i in range(3):
        db.add_guild_member("g_coup", f"npc_supporter_{i}", "npc", "soldier", 0, loyalty=25, ambition=80)
    db.add_guild_member("g_coup", "npc_defender", "npc", "veteran", 0, loyalty=80, ambition=30)
    db.add_guild_member("g_coup", "npc_old_leader", "npc", "leader", 0, loyalty=70, ambition=40)
    db.add_guild_member("g_coup", "npc_actor", "npc", "soldier", 0, loyalty=20, ambition=90)

    guild = guild_repo.get_guild_state(db, "g_coup")
    members = guild_repo.get_members(db, "g_coup")
    intent_coup = AttemptCoupIntent(actor_id="npc_actor", guild_id="g_coup")

    result = adjudicate_intent(intent_coup, guild, members, db, turn=10)

    check("Coup succeeds with strong supporters", result.success, result.narrative)
    check("New leader_id set to actor", result.state_changes.get("current_leader_id") == "npc_actor",
          str(result.state_changes))

    updated = db.get_guild_state("g_coup")
    check("Guild morale reduced by 15", updated["morale"] <= guild.morale - 15,
          f"was {guild.morale}, now {updated['morale']}")
    check("Guild stability reduced by 10", updated["stability"] <= guild.stability - 10,
          f"was {guild.stability}, now {updated['stability']}")

    # Coup failure scenario
    db2 = WorldDatabase(":memory:")
    db2.open()
    db2.add_guild_state(
        guild_id="g_strong", template_id="t1", name="Steel Wall",
        founding_turn=0, headquarters_zone_id="zone", archetype="combat",
    )
    db2.update_guild_state("g_strong", morale=90, stability=80, current_leader_id="npc_chief")
    db2.add_guild_member("g_strong", "npc_chief", "npc", "leader", 0, loyalty=95, ambition=30)
    for i in range(3):
        db2.add_guild_member("g_strong", f"npc_loyal_{i}", "npc", "soldier", 0, loyalty=85, ambition=20)
    db2.add_guild_member("g_strong", "npc_rebel", "npc", "recruit", 0, loyalty=20, ambition=90)

    guild2 = guild_repo.get_guild_state(db2, "g_strong")
    members2 = guild_repo.get_members(db2, "g_strong")
    intent_coup2 = AttemptCoupIntent(actor_id="npc_rebel", guild_id="g_strong")

    result2 = adjudicate_intent(intent_coup2, guild2, members2, db2, turn=5)

    check("Coup fails against high-morale defenders", not result2.success, result2.narrative)

    updated2 = db2.get_guild_state("g_strong")
    check("Leader unchanged after failed coup",
          updated2["current_leader_id"] == "npc_chief",
          updated2["current_leader_id"])

    db.close()
    db2.close()
except Exception:
    print(f"  {FAIL} Run 3 crashed:\n{traceback.format_exc()}")


section("Run 4 — Splinter guild adjudication")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_engine import adjudicate_intent
    from systems.guilds.guild_models import FoundSplinterGuildIntent
    from systems.guilds import guild_repo

    db = WorldDatabase(":memory:")
    db.open()

    db.add_guild_state(
        guild_id="g_fracture", template_id="t1", name="Cracked Shield",
        founding_turn=0, headquarters_zone_id="zone", archetype="combat",
    )
    db.update_guild_state("g_fracture", stability=20, wealth=200, influence=25, current_leader_id="npc_leader")
    db.add_guild_member("g_fracture", "npc_leader", "npc", "leader", 0, loyalty=70, ambition=40)
    for i in range(3):
        db.add_guild_member(
            "g_fracture", f"npc_splitter_{i}", "npc", "soldier", 0, loyalty=35, ambition=75,
        )

    guild = guild_repo.get_guild_state(db, "g_fracture")
    members = guild_repo.get_members(db, "g_fracture")

    intent_split = FoundSplinterGuildIntent(
        actor_id="npc_splitter_0",
        parent_guild_id="g_fracture",
        name="Iron Fracture",
        initial_supporters=["npc_splitter_0", "npc_splitter_1", "npc_splitter_2"],
        new_focus="war",
    )

    result = adjudicate_intent(intent_split, guild, members, db, turn=20)

    check("Splinter succeeds with valid conditions", result.success, result.narrative)
    check("new_guild_id is set", result.new_guild_id is not None, str(result.new_guild_id))

    if result.new_guild_id:
        new_guild = db.get_guild_state(result.new_guild_id)
        check("New guild row exists in guild_state", new_guild is not None)

        new_members = db.get_guild_members(result.new_guild_id)
        check("Supporters moved to new guild",
              any(m["entity_id"].startswith("npc_splitter") for m in new_members))

    parent_after = db.get_guild_state("g_fracture")
    check("Parent stability reduced by 10",
          parent_after["stability"] <= guild.stability - 10,
          f"was {guild.stability}, now {parent_after['stability']}")

    if result.new_guild_id:
        rel = db.get_guild_relation("g_fracture", result.new_guild_id)
        check("Relation between parent and splinter is hostile",
              rel is not None and rel["stance"] == "hostile",
              str(rel["stance"] if rel else "None"))

    # Blocked splinter (too stable)
    db2 = WorldDatabase(":memory:")
    db2.open()
    db2.add_guild_state(
        guild_id="g_solid", template_id="t1", name="Rock Solid",
        founding_turn=0, headquarters_zone_id="zone", archetype="combat",
    )
    db2.update_guild_state("g_solid", stability=60, wealth=50, influence=5)
    db2.add_guild_member("g_solid", "npc_actor2", "npc", "soldier", 0, loyalty=30, ambition=80)

    guild3 = guild_repo.get_guild_state(db2, "g_solid")
    members3 = guild_repo.get_members(db2, "g_solid")

    intent_blocked = FoundSplinterGuildIntent(
        actor_id="npc_actor2",
        parent_guild_id="g_solid",
        name="Breakaway",
        initial_supporters=["npc_actor2"],
        new_focus="idle",
    )

    result3 = adjudicate_intent(intent_blocked, guild3, members3, db2, turn=5)
    check("Splinter blocked when guild is stable", not result3.success, result3.narrative)

    # Verify parent rows unchanged
    parent3 = db2.get_guild_state("g_solid")
    check("Parent stability unchanged after blocked splinter",
          parent3["stability"] == 60, f"stability={parent3['stability']}")

    db.close()
    db2.close()
except Exception:
    print(f"  {FAIL} Run 4 crashed:\n{traceback.format_exc()}")


# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\n{'═' * 60}")
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed
print(f"  Total checks : {total}")
print(f"  {PASS} Passed : {passed}")
if failed:
    print(f"  {FAIL} Failed : {failed}")
    for label, ok, detail in results:
        if not ok:
            print(f"    ✗ {label}" + (f" — {detail}" if detail else ""))
print(f"{'═' * 60}\n")
sys.exit(0 if failed == 0 else 1)
