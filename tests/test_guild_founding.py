"""
Test guild founding flow.

Run with: python tests/test_guild_founding.py
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


section("Run 1 — Player founds a guild (no AI)")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_models import FoundGuildIntent
    from systems.guilds import guild_repo

    db = WorldDatabase(":memory:")
    db.open()

    intent = FoundGuildIntent(
        name="Crimson Ledger",
        archetype="merchant",
        zone_id="verath_market",
        founding_reason="Break the merchant monopoly",
        initial_members=[],
    )

    check("FoundGuildIntent validates correctly", True)

    state = guild_repo.found_guild(
        world_db=db,
        intent=intent,
        template_id="gen_test_template",
        turn=1,
        player_id="player_001",
    )

    check("found_guild() returns a GuildState", state is not None)
    check("guild_state.name matches intent.name",
          state.name == "Crimson Ledger", state.name)
    check("guild_state.archetype matches intent.archetype",
          state.archetype == "merchant", state.archetype)

    members = db.get_guild_members(state.guild_id)
    player_member = next((m for m in members if m["entity_id"] == "player_001"), None)
    check("player is in guild_members", player_member is not None)
    check("player has rank_id == 'leader'",
          player_member is not None and player_member["rank_id"] == "leader",
          player_member["rank_id"] if player_member else "None")

    gs_row = db.get_guild_state(state.guild_id)
    check("guild_state.current_leader_id == player_id",
          gs_row is not None and gs_row["current_leader_id"] == "player_001",
          gs_row["current_leader_id"] if gs_row else "None")

    db.close()
except Exception:
    print(f"  {FAIL} Run 1 crashed:\n{traceback.format_exc()}")


section("Run 2 — NPC founding")
try:
    from persistence.world_db import WorldDatabase
    from systems.guilds.guild_models import FoundGuildIntent
    from systems.guilds import guild_repo

    db = WorldDatabase(":memory:")
    db.open()

    intent2 = FoundGuildIntent(
        name="Shadow Compact",
        archetype="stealth",
        zone_id="verath_undermarket",
        founding_reason="Resist the Iron Vanguard",
        initial_members=["npc_aldis"],
    )

    state2 = guild_repo.found_guild(
        world_db=db,
        intent=intent2,
        template_id="gen_shadow_template",
        turn=5,
        player_id=None,
    )

    members2 = db.get_guild_members(state2.guild_id)
    npc_member = next((m for m in members2 if m["entity_id"] == "npc_aldis"), None)
    check("NPC member row created", npc_member is not None)
    check("NPC member entity_type == 'npc'",
          npc_member is not None and npc_member["entity_type"] == "npc",
          npc_member["entity_type"] if npc_member else "None")

    gs2 = db.get_guild_state(state2.guild_id)
    check("guild_state.founder_entity_id == 'npc_aldis'",
          gs2 is not None and gs2["founder_entity_id"] == "npc_aldis",
          gs2["founder_entity_id"] if gs2 else "None")

    db.close()
except Exception:
    print(f"  {FAIL} Run 2 crashed:\n{traceback.format_exc()}")


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
