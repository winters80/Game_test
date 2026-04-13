"""
Test guild background simulation tick.

Run with: python tests/test_background_tick.py
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


section("Run 1 — select_active_agents")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_sim import select_active_agents

    db = WorldDatabase(":memory:")
    db.open()

    # Two guilds, 2 members each
    for gi, gid in enumerate(["g_a", "g_b"]):
        db.add_guild_state(gid, "t1", f"Guild {gi}", 0, "zone", "combat")
        db.update_guild_state(gid, current_leader_id=f"leader_{gid}")
        db.add_guild_member(gid, f"leader_{gid}", "npc", "leader", 0, loyalty=80, ambition=40)
        db.add_guild_member(gid, f"member_{gid}", "npc", "soldier", 0, loyalty=30, ambition=80)

    agents = select_active_agents(db, max_agents=2)

    check("select_active_agents returns <= max_agents", len(agents) <= 2, str(len(agents)))
    check("returns at least 1 agent", len(agents) >= 1, str(agents))

    # Leader should be among the top agents
    all_ids = [(g, e) for g, e in agents]
    has_leader = any("leader" in e for g, e in all_ids)
    check("A leader is among the selected agents", has_leader, str(all_ids))

    db.close()
except Exception:
    print(f"  {FAIL} Run 1 crashed:\n{traceback.format_exc()}")


section("Run 2 — tick with no AI (nil ai_generator)")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_sim import tick

    db = WorldDatabase(":memory:")
    db.open()

    db.add_guild_state("g_tick", "t1", "Tick Guild", 0, "zone", "combat")
    db.update_guild_state("g_tick", stability=30, morale=40, current_leader_id="npc_lead")
    db.add_guild_member("g_tick", "npc_lead", "npc", "leader", 0, loyalty=20, ambition=90)

    before = db.get_guild_state("g_tick")
    results_tick = tick(world_db=db, guild_registry=None, ai_generator=None, turn=10)

    after = db.get_guild_state("g_tick")

    check("tick() runs without crash when AI is None", True)
    check("tick_last_updated is updated after tick",
          after["tick_last_updated"] == 10,
          f"was {before['tick_last_updated']}, now {after['tick_last_updated']}")
    check("No results when AI is None (no intents generated)", len(results_tick) == 0,
          str(len(results_tick)))

    db.close()
except Exception:
    print(f"  {FAIL} Run 2 crashed:\n{traceback.format_exc()}")


section("Run 3 — Full pipeline with mock AI (StealResourcesIntent)")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_sim import tick
    from systems.guilds.guild_models import StealResourcesIntent

    db = WorldDatabase(":memory:")
    db.open()

    db.add_guild_state("g_steal", "t1", "Steal Guild", 0, "zone", "merchant")
    db.update_guild_state("g_steal", wealth=200, stability=40, current_leader_id="npc_thief")
    db.add_guild_member("g_steal", "npc_thief", "npc", "leader", 0, loyalty=15, ambition=95)

    # Mock AI that always returns a StealResourcesIntent
    class MockAI:
        def generate_guild_intent(self, guild, member):
            return StealResourcesIntent(
                actor_id=member.entity_id,
                guild_id=guild.guild_id,
                amount=50,
            )

    before_wealth = db.get_guild_state("g_steal")["wealth"]
    results_mock = tick(world_db=db, guild_registry=None, ai_generator=MockAI(), turn=15)

    check("tick() returns at least 1 result with mock AI",
          len(results_mock) >= 1, str(len(results_mock)))

    if results_mock:
        r = results_mock[0]
        check("Result is a GuildIntentResult with success=True", r.success, r.narrative)

    after_wealth = db.get_guild_state("g_steal")["wealth"]
    check("Guild wealth reduced by stolen amount",
          after_wealth < before_wealth,
          f"before={before_wealth}, after={after_wealth}")

    db.close()
except Exception:
    print(f"  {FAIL} Run 3 crashed:\n{traceback.format_exc()}")


# ── Summary ─────────────────────────���───────────────────────────────���──────────
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
