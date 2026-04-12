"""
WorldDatabase — SQLite-backed world state for a single save slot.

Rule: Content definitions (classes, items, NPCs templates) live in JSON.
      Everything that CHANGES during play lives here.

Tables created by this module:
  - npc_instances       — live NPC state per save
  - npc_memory          — per-NPC interaction history
  - quest_instances     — active/completed quest state
  - ai_quest_data       — full JSON definition for AI-generated quests
  - faction_standing    — player standing with each faction
  - faction_relations   — faction-to-faction relationship matrix
  - auction_listings    — active auction items / life tokens
  - auction_bids        — bid history per listing
  - death_records       — every player death, for narrative use
  - world_flags         — global world-state flags independent of player
  - turn_log            — lightweight event log for quest/NPC triggers
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


DB_SCHEMA_VERSION = 3

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS db_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- ── NPCs ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS npc_instances (
    npc_id          TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL,
    current_zone_id TEXT,
    current_role    TEXT,
    is_alive        INTEGER DEFAULT 1,
    disposition     REAL DEFAULT 0.0,      -- -100 hostile .. +100 devoted
    last_seen_scene TEXT,
    custom_state    TEXT DEFAULT '{}'      -- JSON blob for NPC-specific flags
);

CREATE TABLE IF NOT EXISTS npc_memory (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id              TEXT NOT NULL,
    event_type          TEXT NOT NULL,     -- "helped","attacked","quest_given","quest_done","betrayed"
    summary             TEXT NOT NULL,
    turn_number         INTEGER DEFAULT 0,
    alignment_snapshot  REAL DEFAULT 0.0,
    is_compressed       INTEGER DEFAULT 0, -- 1 = this row is a compression summary
    FOREIGN KEY (npc_id) REFERENCES npc_instances(npc_id)
);

-- ── Quests ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quest_instances (
    instance_id     TEXT PRIMARY KEY,
    template_id     TEXT,                  -- NULL for fully AI-generated quests
    current_state   TEXT NOT NULL,
    giver_npc_id    TEXT,
    accepted_turn   INTEGER DEFAULT 0,
    completed_turn  INTEGER,
    is_active       INTEGER DEFAULT 1,
    outcome         TEXT,                  -- "success" | "failed" | "abandoned"
    ai_context      TEXT DEFAULT '{}'      -- JSON: player profile at generation time
);

CREATE TABLE IF NOT EXISTS ai_quest_data (
    instance_id     TEXT PRIMARY KEY,
    definition      TEXT NOT NULL,         -- full QuestTemplate JSON for AI-generated quests
    FOREIGN KEY (instance_id) REFERENCES quest_instances(instance_id)
);

-- ── Factions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS faction_standing (
    faction_id  TEXT PRIMARY KEY,
    standing    REAL DEFAULT 0.0,          -- -100 .. +100
    rank        TEXT DEFAULT 'outsider'    -- outsider → member → officer → general → leader → ruler
);

CREATE TABLE IF NOT EXISTS faction_relations (
    faction_a   TEXT NOT NULL,
    faction_b   TEXT NOT NULL,
    relation    REAL DEFAULT 0.0,          -- -100 war .. +100 alliance
    PRIMARY KEY (faction_a, faction_b)
);

-- ── Auction House ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auction_listings (
    listing_id      TEXT PRIMARY KEY,
    auction_guild   TEXT NOT NULL,
    item_id         TEXT,                  -- NULL for life tokens
    item_type       TEXT NOT NULL,         -- "standard" | "rare" | "unique" | "life_token"
    quantity        INTEGER DEFAULT 1,
    current_bid     INTEGER NOT NULL,
    minimum_bid     INTEGER NOT NULL,
    reserve_price   INTEGER,
    seller_npc_id   TEXT,
    expires_turn    INTEGER NOT NULL,
    status          TEXT DEFAULT 'active'  -- "active" | "sold" | "expired"
);

CREATE TABLE IF NOT EXISTS auction_bids (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL,
    bidder      TEXT NOT NULL,             -- "player" or NPC ID
    bid_amount  INTEGER NOT NULL,
    bid_turn    INTEGER NOT NULL,
    FOREIGN KEY (listing_id) REFERENCES auction_listings(listing_id)
);

-- ── Death Records ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS death_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cause               TEXT,
    zone_id             TEXT,
    turn_number         INTEGER DEFAULT 0,
    level_at_death      INTEGER DEFAULT 1,
    alignment_at_death  REAL DEFAULT 0.0,
    lives_remaining     INTEGER DEFAULT 0
);

-- ── World State ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_flags (
    flag_key    TEXT PRIMARY KEY,
    flag_value  TEXT NOT NULL DEFAULT 'true',
    set_turn    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turn_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_number INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT DEFAULT '{}'
);

-- ── AI Generated Content ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_generated_content (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type    TEXT NOT NULL,      -- "quest" | "narrative" | "npc_branch"
    content_id      TEXT NOT NULL,      -- zone_id, npc_id, or quest template_id
    definition      TEXT NOT NULL,      -- full JSON blob
    zone_id         TEXT,
    generated_turn  INTEGER DEFAULT 0,
    integrated      INTEGER DEFAULT 0   -- 0 = pending, 1 = integrated into live game
);
-- ── AI Generated Skills ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_generated_skills (
    skill_id        TEXT PRIMARY KEY,
    definition      TEXT NOT NULL,
    source          TEXT DEFAULT '',
    generated_turn  INTEGER DEFAULT 0
);
-- ── Bot Instances ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_instances (
    bot_id          TEXT PRIMARY KEY,
    definition      TEXT NOT NULL,
    current_zone_id TEXT,
    last_active_turn INTEGER DEFAULT 0
);
"""


class WorldDatabase:
    """
    Thin wrapper around a per-save-slot SQLite database.
    All methods are explicit and typed — no ORM magic.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Connection lifecycle ───────────────────────────────────────────────────

    def open(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._ensure_meta()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        assert self._conn, "Database not open"
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _ensure_meta(self) -> None:
        assert self._conn
        row = self._conn.execute(
            "SELECT value FROM db_meta WHERE key = 'schema_version'"
        ).fetchone()
        if not row:
            self._conn.execute(
                "INSERT INTO db_meta VALUES ('schema_version', ?)",
                (str(DB_SCHEMA_VERSION),),
            )
            self._conn.commit()
        else:
            stored = int(row["value"])
            if stored < DB_SCHEMA_VERSION:
                self._migrate(stored)

    def _migrate(self, from_version: int) -> None:
        """Apply incremental migrations from from_version to DB_SCHEMA_VERSION."""
        assert self._conn
        if from_version < 2:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS ai_generated_content (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_type    TEXT NOT NULL,
                    content_id      TEXT NOT NULL,
                    definition      TEXT NOT NULL,
                    zone_id         TEXT,
                    generated_turn  INTEGER DEFAULT 0,
                    integrated      INTEGER DEFAULT 0
                );
            """)
        if from_version < 3:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS ai_generated_skills (
                    skill_id        TEXT PRIMARY KEY,
                    definition      TEXT NOT NULL,
                    source          TEXT DEFAULT '',
                    generated_turn  INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS bot_instances (
                    bot_id          TEXT PRIMARY KEY,
                    definition      TEXT NOT NULL,
                    current_zone_id TEXT,
                    last_active_turn INTEGER DEFAULT 0
                );
            """)
            self._conn.execute("UPDATE db_meta SET value = 3 WHERE key = 'schema_version'")
            self._conn.commit()
        self._conn.execute(
            "INSERT OR REPLACE INTO db_meta VALUES ('schema_version', ?)",
            (str(DB_SCHEMA_VERSION),),
        )
        self._conn.commit()

    # ── NPC ───────────────────────────────────────────────────────────────────

    def upsert_npc(self, npc_id: str, template_id: str, zone_id: str, role: str) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO npc_instances (npc_id, template_id, current_zone_id, current_role)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(npc_id) DO NOTHING""",
            (npc_id, template_id, zone_id, role),
        )
        self._conn.commit()

    def get_npc(self, npc_id: str) -> dict[str, Any] | None:
        assert self._conn
        row = self._conn.execute(
            "SELECT * FROM npc_instances WHERE npc_id = ?", (npc_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_npc_disposition(self, npc_id: str, delta: float) -> None:
        assert self._conn
        self._conn.execute(
            """UPDATE npc_instances
               SET disposition = MAX(-100.0, MIN(100.0, disposition + ?))
               WHERE npc_id = ?""",
            (delta, npc_id),
        )
        self._conn.commit()

    def set_npc_state(self, npc_id: str, key: str, value: Any) -> None:
        """Set a key in the NPC's custom_state JSON blob."""
        assert self._conn
        row = self._conn.execute(
            "SELECT custom_state FROM npc_instances WHERE npc_id = ?", (npc_id,)
        ).fetchone()
        if not row:
            return
        state = json.loads(row["custom_state"] or "{}")
        state[key] = value
        self._conn.execute(
            "UPDATE npc_instances SET custom_state = ? WHERE npc_id = ?",
            (json.dumps(state), npc_id),
        )
        self._conn.commit()

    def add_npc_memory(
        self, npc_id: str, event_type: str, summary: str,
        turn_number: int = 0, alignment_snapshot: float = 0.0,
    ) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO npc_memory
               (npc_id, event_type, summary, turn_number, alignment_snapshot)
               VALUES (?, ?, ?, ?, ?)""",
            (npc_id, event_type, summary, turn_number, alignment_snapshot),
        )
        self._conn.commit()

    def get_npc_memory(self, npc_id: str, limit: int = 20) -> list[dict[str, Any]]:
        assert self._conn
        rows = self._conn.execute(
            """SELECT * FROM npc_memory WHERE npc_id = ?
               ORDER BY id DESC LIMIT ?""",
            (npc_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def count_npc_memory(self, npc_id: str) -> int:
        assert self._conn
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM npc_memory WHERE npc_id = ? AND is_compressed = 0",
            (npc_id,),
        ).fetchone()
        return row["c"] if row else 0

    def compress_npc_memory(self, npc_id: str, summary: str, up_to_id: int) -> None:
        """Replace memory rows up to up_to_id with a single compressed summary."""
        assert self._conn
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM npc_memory WHERE npc_id = ? AND id <= ? AND is_compressed = 0",
                (npc_id, up_to_id),
            )
            conn.execute(
                """INSERT INTO npc_memory
                   (npc_id, event_type, summary, is_compressed)
                   VALUES (?, 'compressed_history', ?, 1)""",
                (npc_id, summary),
            )

    # ── Quests ────────────────────────────────────────────────────────────────

    def add_quest(
        self, instance_id: str, template_id: str | None,
        initial_state: str, giver_npc_id: str | None,
        turn_number: int = 0, ai_context: dict | None = None,
    ) -> None:
        assert self._conn
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO quest_instances
                   (instance_id, template_id, current_state, giver_npc_id,
                    accepted_turn, ai_context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    instance_id, template_id, initial_state, giver_npc_id,
                    turn_number, json.dumps(ai_context or {}),
                ),
            )

    def store_ai_quest(self, instance_id: str, definition: dict) -> None:
        assert self._conn
        self._conn.execute(
            "INSERT OR REPLACE INTO ai_quest_data VALUES (?, ?)",
            (instance_id, json.dumps(definition)),
        )
        self._conn.commit()

    def get_quest(self, instance_id: str) -> dict[str, Any] | None:
        assert self._conn
        row = self._conn.execute(
            "SELECT * FROM quest_instances WHERE instance_id = ?", (instance_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_active_quests(self) -> list[dict[str, Any]]:
        assert self._conn
        rows = self._conn.execute(
            "SELECT * FROM quest_instances WHERE is_active = 1"
        ).fetchall()
        return [dict(r) for r in rows]

    def advance_quest(self, instance_id: str, new_state: str) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE quest_instances SET current_state = ? WHERE instance_id = ?",
            (new_state, instance_id),
        )
        self._conn.commit()

    def complete_quest(self, instance_id: str, outcome: str, turn_number: int) -> None:
        assert self._conn
        self._conn.execute(
            """UPDATE quest_instances
               SET is_active = 0, outcome = ?, completed_turn = ?
               WHERE instance_id = ?""",
            (outcome, turn_number, instance_id),
        )
        self._conn.commit()

    # ── Factions ──────────────────────────────────────────────────────────────

    def get_faction_standing(self, faction_id: str) -> tuple[float, str]:
        assert self._conn
        row = self._conn.execute(
            "SELECT standing, rank FROM faction_standing WHERE faction_id = ?",
            (faction_id,),
        ).fetchone()
        return (row["standing"], row["rank"]) if row else (0.0, "outsider")

    def update_faction_standing(self, faction_id: str, delta: float) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO faction_standing (faction_id, standing)
               VALUES (?, ?)
               ON CONFLICT(faction_id) DO UPDATE SET
                 standing = MAX(-100.0, MIN(100.0, standing + excluded.standing))""",
            (faction_id, delta),
        )
        self._conn.commit()

    def set_faction_rank(self, faction_id: str, rank: str) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO faction_standing (faction_id, rank)
               VALUES (?, ?)
               ON CONFLICT(faction_id) DO UPDATE SET rank = excluded.rank""",
            (faction_id, rank),
        )
        self._conn.commit()

    def get_faction_relation(self, faction_a: str, faction_b: str) -> float:
        assert self._conn
        # Normalise order so (a,b) and (b,a) are the same row
        a, b = sorted([faction_a, faction_b])
        row = self._conn.execute(
            "SELECT relation FROM faction_relations WHERE faction_a = ? AND faction_b = ?",
            (a, b),
        ).fetchone()
        return row["relation"] if row else 0.0

    def update_faction_relation(self, faction_a: str, faction_b: str, delta: float) -> None:
        assert self._conn
        a, b = sorted([faction_a, faction_b])
        self._conn.execute(
            """INSERT INTO faction_relations (faction_a, faction_b, relation)
               VALUES (?, ?, ?)
               ON CONFLICT(faction_a, faction_b) DO UPDATE SET
                 relation = MAX(-100.0, MIN(100.0, relation + excluded.relation))""",
            (a, b, delta),
        )
        self._conn.commit()

    # ── Auction House ─────────────────────────────────────────────────────────

    def add_auction_listing(
        self, listing_id: str, auction_guild: str, item_id: str | None,
        item_type: str, quantity: int, starting_bid: int,
        expires_turn: int, seller_npc_id: str | None = None,
        reserve_price: int | None = None,
    ) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO auction_listings
               (listing_id, auction_guild, item_id, item_type, quantity,
                current_bid, minimum_bid, reserve_price, seller_npc_id, expires_turn)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing_id, auction_guild, item_id, item_type, quantity,
                starting_bid, starting_bid, reserve_price, seller_npc_id, expires_turn,
            ),
        )
        self._conn.commit()

    def place_bid(self, listing_id: str, bidder: str, amount: int, turn: int) -> bool:
        """Place a bid. Returns True if bid was accepted (higher than current)."""
        assert self._conn
        row = self._conn.execute(
            "SELECT current_bid, status FROM auction_listings WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
        if not row or row["status"] != "active":
            return False
        if amount <= row["current_bid"]:
            return False
        with self.transaction() as conn:
            conn.execute(
                "UPDATE auction_listings SET current_bid = ? WHERE listing_id = ?",
                (amount, listing_id),
            )
            conn.execute(
                "INSERT INTO auction_bids (listing_id, bidder, bid_amount, bid_turn) VALUES (?, ?, ?, ?)",
                (listing_id, bidder, amount, turn),
            )
        return True

    def get_active_listings(self, item_type: str | None = None) -> list[dict[str, Any]]:
        assert self._conn
        if item_type:
            rows = self._conn.execute(
                "SELECT * FROM auction_listings WHERE status = 'active' AND item_type = ?",
                (item_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM auction_listings WHERE status = 'active'"
            ).fetchall()
        return [dict(r) for r in rows]

    def expire_listings(self, current_turn: int) -> list[dict[str, Any]]:
        """Close all listings past their expiry. Returns the expired listings."""
        assert self._conn
        rows = self._conn.execute(
            "SELECT * FROM auction_listings WHERE status = 'active' AND expires_turn <= ?",
            (current_turn,),
        ).fetchall()
        expired = [dict(r) for r in rows]
        if expired:
            ids = [r["listing_id"] for r in expired]
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE auction_listings SET status = 'expired' WHERE listing_id IN ({placeholders})",
                ids,
            )
            self._conn.commit()
        return expired

    # ── Death Records ─────────────────────────────────────────────────────────

    def record_death(
        self, cause: str, zone_id: str, turn_number: int,
        level_at_death: int, alignment_at_death: float, lives_remaining: int,
    ) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO death_records
               (cause, zone_id, turn_number, level_at_death, alignment_at_death, lives_remaining)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cause, zone_id, turn_number, level_at_death, alignment_at_death, lives_remaining),
        )
        self._conn.commit()

    def get_death_count(self) -> int:
        assert self._conn
        row = self._conn.execute("SELECT COUNT(*) as c FROM death_records").fetchone()
        return row["c"] if row else 0

    # ── World Flags ───────────────────────────────────────────────────────────

    def set_world_flag(self, key: str, value: str = "true", turn: int = 0) -> None:
        assert self._conn
        self._conn.execute(
            "INSERT OR REPLACE INTO world_flags VALUES (?, ?, ?)",
            (key, value, turn),
        )
        self._conn.commit()

    def get_world_flag(self, key: str) -> str | None:
        assert self._conn
        row = self._conn.execute(
            "SELECT flag_value FROM world_flags WHERE flag_key = ?", (key,)
        ).fetchone()
        return row["flag_value"] if row else None

    def has_world_flag(self, key: str) -> bool:
        return self.get_world_flag(key) is not None

    # ── Turn Log ──────────────────────────────────────────────────────────────

    def log_event(self, turn_number: int, event_type: str, payload: dict | None = None) -> None:
        assert self._conn
        self._conn.execute(
            "INSERT INTO turn_log (turn_number, event_type, payload) VALUES (?, ?, ?)",
            (turn_number, event_type, json.dumps(payload or {})),
        )
        self._conn.commit()

    # ── AI Generated Content ──────────────────────────────────────────────────

    def store_bg_content(
        self, content_type: str, content_id: str, definition: dict,
        zone_id: str | None = None, generated_turn: int = 0,
    ) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT INTO ai_generated_content
               (content_type, content_id, definition, zone_id, generated_turn)
               VALUES (?, ?, ?, ?, ?)""",
            (content_type, content_id, json.dumps(definition), zone_id, generated_turn),
        )
        self._conn.commit()

    def get_unintegrated_bg_content(self, content_type: str | None = None) -> list[dict[str, Any]]:
        assert self._conn
        if content_type:
            rows = self._conn.execute(
                "SELECT * FROM ai_generated_content WHERE integrated = 0 AND content_type = ?",
                (content_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM ai_generated_content WHERE integrated = 0"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_bg_content_integrated(self, row_id: int) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE ai_generated_content SET integrated = 1 WHERE id = ?",
            (row_id,),
        )
        self._conn.commit()

    # ── AI-generated skills ───────────────────────────────────────────────────────

    def store_ai_skill(self, skill_id: str, definition: dict, source: str, generated_turn: int) -> None:
        """Persist an AI-generated skill definition."""
        assert self._conn
        self._conn.execute(
            "INSERT OR REPLACE INTO ai_generated_skills VALUES (?, ?, ?, ?)",
            (skill_id, json.dumps(definition), source, generated_turn),
        )
        self._conn.commit()

    def load_ai_skills(self) -> list[dict]:
        """Load all AI-generated skill definitions as raw dicts."""
        assert self._conn
        rows = self._conn.execute("SELECT definition FROM ai_generated_skills").fetchall()
        return [json.loads(r["definition"]) for r in rows]

    # ── Bot instances ─────────────────────────────────────────────────────────────

    def upsert_bot_instance(self, bot_id: str, definition: dict, current_zone_id: str, last_active_turn: int) -> None:
        """Save or update a bot agent's state."""
        assert self._conn
        self._conn.execute(
            """INSERT INTO bot_instances (bot_id, definition, current_zone_id, last_active_turn)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(bot_id) DO UPDATE SET
                 definition = excluded.definition,
                 current_zone_id = excluded.current_zone_id,
                 last_active_turn = excluded.last_active_turn""",
            (bot_id, json.dumps(definition), current_zone_id, last_active_turn),
        )
        self._conn.commit()

    def load_bot_instances(self) -> list[dict]:
        """Load all bot agent rows as plain dicts."""
        assert self._conn
        rows = self._conn.execute("SELECT * FROM bot_instances").fetchall()
        return [dict(r) for r in rows]
