"""
Test DB migrations and guild table schema.

Run with: python tests/test_db_migrations.py
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


section("Run 1 — Schema creation (in-memory DB)")
try:
    from persistence.world_db import WorldDatabase, DB_SCHEMA_VERSION
    db = WorldDatabase(":memory:")
    db.open()

    check("DB_SCHEMA_VERSION == 9", DB_SCHEMA_VERSION == 9, str(DB_SCHEMA_VERSION))

    # Check tables exist via sqlite_master
    def table_exists(d, name):
        row = d._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    check("guild_state table exists", table_exists(db, "guild_state"))
    check("guild_members table exists", table_exists(db, "guild_members"))
    check("guild_relations table exists", table_exists(db, "guild_relations"))
    check("guild_projects table exists", table_exists(db, "guild_projects"))

    meta_row = db._conn.execute(
        "SELECT value FROM db_meta WHERE key = 'schema_version'"
    ).fetchone()
    check("db_meta schema_version == 9", meta_row is not None and meta_row["value"] == "9",
          str(meta_row["value"] if meta_row else "None"))

    db.close()
except Exception:
    print(f"  {FAIL} Run 1 crashed:\n{traceback.format_exc()}")


section("Run 2 — Migration from v4 to current")
try:
    import sqlite3, tempfile, os
    from persistence.world_db import DB_SCHEMA_VERSION

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name

    # Create a v4 database manually (no guild tables)
    conn = sqlite3.connect(tmp_path)
    conn.execute("CREATE TABLE IF NOT EXISTS db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO db_meta VALUES ('schema_version', '4')")
    conn.execute("""CREATE TABLE IF NOT EXISTS world_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        zone_id TEXT,
        event_text TEXT NOT NULL,
        title TEXT DEFAULT '',
        npc_hint TEXT DEFAULT '',
        generated_turn INTEGER DEFAULT 0,
        shown INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

    # Now open with WorldDatabase — should migrate to the current version
    db2 = WorldDatabase(tmp_path)
    db2.open()

    def table_exists2(d, name):
        row = d._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    check("Migration runs without error", True)
    check("guild_state created after migration", table_exists2(db2, "guild_state"))
    check("guild_members created after migration", table_exists2(db2, "guild_members"))
    check("world_actions created after migration (v6)", table_exists2(db2, "world_actions"))
    check("world_expansions created after migration (v6)", table_exists2(db2, "world_expansions"))
    check("dungeon_lairs created after migration (v7)", table_exists2(db2, "dungeon_lairs"))
    check("lair_history created after migration (v7)", table_exists2(db2, "lair_history"))
    check("generated_dungeons created after migration (v8)", table_exists2(db2, "generated_dungeons"))
    idx = {r[0] for r in db2._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    check("v9 indexes exist after migration", {"ix_npc_memory_npc", "ix_world_events_shown"} <= idx, str(sorted(idx)))
    import threading as _th
    _errs = []
    def _bg():
        try:
            db2._conn.execute("SELECT COUNT(*) FROM npc_instances").fetchone()
            with db2.transaction() as c:
                c.execute("INSERT OR REPLACE INTO world_flags VALUES ('bg_test', 'true', 0)")
        except Exception as e:
            _errs.append(repr(e))
    _t = _th.Thread(target=_bg); _t.start(); _t.join()
    check("a worker thread can use the world DB (guild sim, world growth)", not _errs, str(_errs))
    for _i in range(30):
        db2._conn.execute("INSERT INTO turn_log (turn_number, event_type) VALUES (?, 'x')", (_i,))
    db2._conn.commit()
    removed = db2.prune(keep_turn_log=10)
    left = db2._conn.execute("SELECT COUNT(*) FROM turn_log").fetchone()[0]
    check("prune keeps only the newest log rows", left == 10 and removed >= 20, f"left={left} removed={removed}")
    check("integrity check passes on a sound file", db2.integrity_ok())
    _bk = Path(tempfile.mkdtemp()) / "b.db"
    check("online backup writes a copy", db2.backup_to(_bk) and _bk.stat().st_size > 0)

    meta_row2 = db2._conn.execute(
        "SELECT value FROM db_meta WHERE key = 'schema_version'"
    ).fetchone()
    check("schema_version updated to 9", meta_row2 is not None and meta_row2["value"] == "9",
          str(meta_row2["value"] if meta_row2 else "None"))

    db2.close()
    os.unlink(tmp_path)
except Exception:
    print(f"  {FAIL} Run 2 crashed:\n{traceback.format_exc()}")


# ── Run 3: saves are atomic, backed up, and a damaged DB is restored ──────────
print("\n  Run 3: save backups + damaged DB recovery")
try:
    import tempfile as _tf
    from entities.player import Player as _P
    from persistence import save_manager as _sm
    _dir = Path(_tf.mkdtemp())
    _st = _sm.new_game_state(_P(name="Bak"), _dir, "bak")
    _st.world_db._conn.execute("INSERT INTO world_flags VALUES ('marker', 'true', 0)")
    _st.world_db._conn.commit()
    _sm.save_game(_st, "bak", _dir)
    check("save writes no leftover temp file", not list(_dir.glob("*.tmp")))
    check("save keeps a backup (json + db)",
          (_dir / "backups" / "bak.1.json").exists() and (_dir / "backups" / "bak.1.db").exists())
    _sm.close_game(_st)
    for side in ("-wal", "-shm"):
        Path(str(_dir / "bak.db") + side).unlink(missing_ok=True)
    (_dir / "bak.db").write_bytes(b"this is not a database" * 100)
    _st2 = _sm.load_game("bak", _dir)
    _row = _st2.world_db._conn.execute("SELECT flag_key FROM world_flags WHERE flag_key='marker'").fetchone()
    check("a damaged world DB is restored from the newest backup", _row is not None)
    check("the damaged file is kept aside", bool(list(_dir.glob("bak.damaged-*.db"))))
    _sm.close_game(_st2)
except Exception:
    print(f"  {FAIL} Run 3 crashed:\n{traceback.format_exc()}")
    results.append(("Run 3", False, "crashed"))


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
