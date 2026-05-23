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


DB_SCHEMA_VERSION = 5

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

-- ── World Events (AI-generated living world feed) ─────────────────────────────
CREATE TABLE IF NOT EXISTS world_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type     TEXT NOT NULL,    -- "world_event" | "rumor" | "lore_entry" | "area_activity"
    zone_id        TEXT,
    event_text     TEXT NOT NULL,
    title          TEXT DEFAULT '',
    npc_hint       TEXT DEFAULT '',
    generated_turn INTEGER DEFAULT 0,
    shown          INTEGER DEFAULT 0
);

-- ── Guild Runtime State ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS guild_state (
    guild_id             TEXT PRIMARY KEY,
    template_id          TEXT NOT NULL,
    name                 TEXT NOT NULL,
    founding_turn        INTEGER NOT NULL,
    founding_reason      TEXT DEFAULT '',
    founder_entity_id    TEXT DEFAULT '',
    headquarters_zone_id TEXT NOT NULL,
    current_leader_id    TEXT DEFAULT '',
    archetype            TEXT NOT NULL,
    lifecycle_state      TEXT NOT NULL DEFAULT 'active',
    morale               INTEGER NOT NULL DEFAULT 50,
    stability            INTEGER NOT NULL DEFAULT 50,
    influence            INTEGER NOT NULL DEFAULT 10,
    secrecy              INTEGER NOT NULL DEFAULT 50,
    wealth               INTEGER NOT NULL DEFAULT 0,
    current_focus        TEXT NOT NULL DEFAULT 'idle',
    tick_last_updated    INTEGER NOT NULL DEFAULT 0,
    is_player_founded    INTEGER NOT NULL DEFAULT 0,
    is_hidden            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS guild_members (
    guild_id             TEXT NOT NULL,
    entity_id            TEXT NOT NULL,
    entity_type          TEXT NOT NULL,
    rank_id              TEXT NOT NULL,
    standing             INTEGER NOT NULL DEFAULT 0,
    loyalty              INTEGER NOT NULL DEFAULT 50,
    ambition             INTEGER NOT NULL DEFAULT 50,
    joined_turn          INTEGER NOT NULL,
    last_promotion_turn  INTEGER,
    PRIMARY KEY (guild_id, entity_id),
    FOREIGN KEY (guild_id) REFERENCES guild_state(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guild_relations (
    guild_id_1        TEXT NOT NULL,
    guild_id_2        TEXT NOT NULL,
    stance            TEXT NOT NULL DEFAULT 'neutral',
    trust             INTEGER NOT NULL DEFAULT 50,
    hostility         INTEGER NOT NULL DEFAULT 0,
    tension           INTEGER NOT NULL DEFAULT 0,
    last_changed_turn INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id_1, guild_id_2)
);

CREATE TABLE IF NOT EXISTS guild_projects (
    project_id      TEXT PRIMARY KEY,
    guild_id        TEXT NOT NULL,
    project_type    TEXT NOT NULL,
    target_id       TEXT DEFAULT '',
    progress        INTEGER NOT NULL DEFAULT 0,
    risk            INTEGER NOT NULL DEFAULT 10,
    lead_entity_id  TEXT DEFAULT '',
    started_turn    INTEGER NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_state(guild_id) ON DELETE CASCADE
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
        if from_version < 4:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS world_events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type     TEXT NOT NULL,
                    zone_id        TEXT,
                    event_text     TEXT NOT NULL,
                    title          TEXT DEFAULT '',
                    npc_hint       TEXT DEFAULT '',
                    generated_turn INTEGER DEFAULT 0,
                    shown          INTEGER DEFAULT 0
                );
            """)
        if from_version < 5:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS guild_state (
                    guild_id             TEXT PRIMARY KEY,
                    template_id          TEXT NOT NULL,
                    name                 TEXT NOT NULL,
                    founding_turn        INTEGER NOT NULL,
                    founding_reason      TEXT DEFAULT '',
                    founder_entity_id    TEXT DEFAULT '',
                    headquarters_zone_id TEXT NOT NULL,
                    current_leader_id    TEXT DEFAULT '',
                    archetype            TEXT NOT NULL,
                    lifecycle_state      TEXT NOT NULL DEFAULT 'active',
                    morale               INTEGER NOT NULL DEFAULT 50,
                    stability            INTEGER NOT NULL DEFAULT 50,
                    influence            INTEGER NOT NULL DEFAULT 10,
                    secrecy              INTEGER NOT NULL DEFAULT 50,
                    wealth               INTEGER NOT NULL DEFAULT 0,
                    current_focus        TEXT NOT NULL DEFAULT 'idle',
                    tick_last_updated    INTEGER NOT NULL DEFAULT 0,
                    is_player_founded    INTEGER NOT NULL DEFAULT 0,
                    is_hidden            INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS guild_members (
                    guild_id             TEXT NOT NULL,
                    entity_id            TEXT NOT NULL,
                    entity_type          TEXT NOT NULL,
                    rank_id              TEXT NOT NULL,
                    standing             INTEGER NOT NULL DEFAULT 0,
                    loyalty              INTEGER NOT NULL DEFAULT 50,
                    ambition             INTEGER NOT NULL DEFAULT 50,
                    joined_turn          INTEGER NOT NULL,
                    last_promotion_turn  INTEGER,
                    PRIMARY KEY (guild_id, entity_id),
                    FOREIGN KEY (guild_id) REFERENCES guild_state(guild_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS guild_relations (
                    guild_id_1        TEXT NOT NULL,
                    guild_id_2        TEXT NOT NULL,
                    stance            TEXT NOT NULL DEFAULT 'neutral',
                    trust             INTEGER NOT NULL DEFAULT 50,
                    hostility         INTEGER NOT NULL DEFAULT 0,
                    tension           INTEGER NOT NULL DEFAULT 0,
                    last_changed_turn INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id_1, guild_id_2)
                );
                CREATE TABLE IF NOT EXISTS guild_projects (
                    project_id      TEXT PRIMARY KEY,
                    guild_id        TEXT NOT NULL,
                    project_type    TEXT NOT NULL,
                    target_id       TEXT DEFAULT '',
                    progress        INTEGER NOT NULL DEFAULT 0,
                    risk            INTEGER NOT NULL DEFAULT 10,
                    lead_entity_id  TEXT DEFAULT '',
                    started_turn    INTEGER NOT NULL,
                    FOREIGN KEY (guild_id) REFERENCES guild_state(guild_id) ON DELETE CASCADE
                );
            """)
        self._conn.execute(
            "INSERT OR REPLACE INTO db_meta VALUES ('schema_version', ?)",
            (str(DB_SCHEMA_VERSION),),
        )
        self._conn.commit()

    # ── NPC (delegates to persistence/repos/npc_repo.py) ─────────────────────

    def upsert_npc(self, npc_id: str, template_id: str, zone_id: str, role: str) -> None:
        from persistence.repos import npc_repo
        assert self._conn
        npc_repo.upsert_npc(self._conn, npc_id, template_id, zone_id, role)

    def get_npc(self, npc_id: str) -> dict[str, Any] | None:
        from persistence.repos import npc_repo
        assert self._conn
        return npc_repo.get_npc(self._conn, npc_id)

    def update_npc_disposition(self, npc_id: str, delta: float) -> None:
        from persistence.repos import npc_repo
        assert self._conn
        npc_repo.update_npc_disposition(self._conn, npc_id, delta)

    def set_npc_state(self, npc_id: str, key: str, value: Any) -> None:
        from persistence.repos import npc_repo
        assert self._conn
        npc_repo.set_npc_state(self._conn, npc_id, key, value)

    def add_npc_memory(
        self, npc_id: str, event_type: str, summary: str,
        turn_number: int = 0, alignment_snapshot: float = 0.0,
    ) -> None:
        from persistence.repos import npc_repo
        assert self._conn
        npc_repo.add_npc_memory(
            self._conn, npc_id, event_type, summary,
            turn_number=turn_number, alignment_snapshot=alignment_snapshot,
        )

    def get_npc_memory(self, npc_id: str, limit: int = 20) -> list[dict[str, Any]]:
        from persistence.repos import npc_repo
        assert self._conn
        return npc_repo.get_npc_memory(self._conn, npc_id, limit=limit)

    def count_npc_memory(self, npc_id: str) -> int:
        from persistence.repos import npc_repo
        assert self._conn
        return npc_repo.count_npc_memory(self._conn, npc_id)

    def compress_npc_memory(self, npc_id: str, summary: str, up_to_id: int) -> None:
        from persistence.repos import npc_repo
        assert self._conn
        npc_repo.compress_npc_memory(self._conn, npc_id, summary, up_to_id)

    # ── Quests (delegates to persistence/repos/quest_repo.py) ────────────────

    def add_quest(
        self, instance_id: str, template_id: str | None,
        initial_state: str, giver_npc_id: str | None,
        turn_number: int = 0, ai_context: dict | None = None,
    ) -> None:
        from persistence.repos import quest_repo
        assert self._conn
        quest_repo.add_quest(
            self._conn, instance_id, template_id, initial_state, giver_npc_id,
            turn_number=turn_number, ai_context=ai_context,
        )

    def store_ai_quest(self, instance_id: str, definition: dict) -> None:
        from persistence.repos import quest_repo
        assert self._conn
        quest_repo.store_ai_quest(self._conn, instance_id, definition)

    def get_ai_quest_definition(self, instance_id: str) -> dict | None:
        from persistence.repos import quest_repo
        assert self._conn
        return quest_repo.get_ai_quest_definition(self._conn, instance_id)

    def get_quest(self, instance_id: str) -> dict[str, Any] | None:
        from persistence.repos import quest_repo
        assert self._conn
        return quest_repo.get_quest(self._conn, instance_id)

    def get_active_quests(self) -> list[dict[str, Any]]:
        from persistence.repos import quest_repo
        assert self._conn
        return quest_repo.get_active_quests(self._conn)

    def advance_quest(self, instance_id: str, new_state: str) -> None:
        from persistence.repos import quest_repo
        assert self._conn
        quest_repo.advance_quest(self._conn, instance_id, new_state)

    def complete_quest(self, instance_id: str, outcome: str, turn_number: int) -> None:
        from persistence.repos import quest_repo
        assert self._conn
        quest_repo.complete_quest(self._conn, instance_id, outcome, turn_number)

    # ── Factions (delegates to persistence/repos/faction_repo.py) ────────────

    def get_faction_standing(self, faction_id: str) -> tuple[float, str]:
        from persistence.repos import faction_repo
        assert self._conn
        return faction_repo.get_faction_standing(self._conn, faction_id)

    def update_faction_standing(self, faction_id: str, delta: float) -> None:
        from persistence.repos import faction_repo
        assert self._conn
        faction_repo.update_faction_standing(self._conn, faction_id, delta)

    def set_faction_rank(self, faction_id: str, rank: str) -> None:
        from persistence.repos import faction_repo
        assert self._conn
        faction_repo.set_faction_rank(self._conn, faction_id, rank)

    def get_faction_relation(self, faction_a: str, faction_b: str) -> float:
        from persistence.repos import faction_repo
        assert self._conn
        return faction_repo.get_faction_relation(self._conn, faction_a, faction_b)

    def update_faction_relation(self, faction_a: str, faction_b: str, delta: float) -> None:
        from persistence.repos import faction_repo
        assert self._conn
        faction_repo.update_faction_relation(self._conn, faction_a, faction_b, delta)

    def get_all_faction_relations(self) -> list[dict]:
        from persistence.repos import faction_repo
        assert self._conn
        return faction_repo.get_all_faction_relations(self._conn)

    def set_faction_relation(self, faction_a: str, faction_b: str, relation: float) -> None:
        from persistence.repos import faction_repo
        assert self._conn
        faction_repo.set_faction_relation(self._conn, faction_a, faction_b, relation)

    # ── Auction House (delegates to persistence/repos/auction_repo.py) ───────

    def add_auction_listing(
        self, listing_id: str, auction_guild: str, item_id: str | None,
        item_type: str, quantity: int, starting_bid: int,
        expires_turn: int, seller_npc_id: str | None = None,
        reserve_price: int | None = None,
    ) -> None:
        from persistence.repos import auction_repo
        assert self._conn
        auction_repo.add_auction_listing(
            self._conn, listing_id, auction_guild, item_id, item_type, quantity,
            starting_bid, expires_turn,
            seller_npc_id=seller_npc_id, reserve_price=reserve_price,
        )

    def place_bid(self, listing_id: str, bidder: str, amount: int, turn: int) -> bool:
        from persistence.repos import auction_repo
        assert self._conn
        return auction_repo.place_bid(self._conn, listing_id, bidder, amount, turn)

    def get_active_listings(self, item_type: str | None = None) -> list[dict[str, Any]]:
        from persistence.repos import auction_repo
        assert self._conn
        return auction_repo.get_active_listings(self._conn, item_type=item_type)

    def expire_listings(self, current_turn: int) -> list[dict[str, Any]]:
        from persistence.repos import auction_repo
        assert self._conn
        return auction_repo.expire_listings(self._conn, current_turn)

    # ── Death Records (delegates to persistence/repos/death_repo.py) ─────────

    def record_death(
        self, cause: str, zone_id: str, turn_number: int,
        level_at_death: int, alignment_at_death: float, lives_remaining: int,
    ) -> None:
        from persistence.repos import death_repo
        assert self._conn
        death_repo.record_death(
            self._conn, cause, zone_id, turn_number,
            level_at_death, alignment_at_death, lives_remaining,
        )

    def get_death_count(self) -> int:
        from persistence.repos import death_repo
        assert self._conn
        return death_repo.get_death_count(self._conn)

    # ── World Flags + Turn Log (delegates to world_state_repo.py) ────────────

    def set_world_flag(self, key: str, value: str = "true", turn: int = 0) -> None:
        from persistence.repos import world_state_repo
        assert self._conn
        world_state_repo.set_world_flag(self._conn, key, value=value, turn=turn)

    def get_world_flag(self, key: str) -> str | None:
        from persistence.repos import world_state_repo
        assert self._conn
        return world_state_repo.get_world_flag(self._conn, key)

    def has_world_flag(self, key: str) -> bool:
        return self.get_world_flag(key) is not None

    def log_event(self, turn_number: int, event_type: str, payload: dict | None = None) -> None:
        from persistence.repos import world_state_repo
        assert self._conn
        world_state_repo.log_event(self._conn, turn_number, event_type, payload=payload)

    # ── AI generated content + skills + world events (ai_content_repo.py) ────

    def store_bg_content(
        self, content_type: str, content_id: str, definition: dict,
        zone_id: str | None = None, generated_turn: int = 0,
    ) -> None:
        from persistence.repos import ai_content_repo
        assert self._conn
        ai_content_repo.store_bg_content(
            self._conn, content_type, content_id, definition,
            zone_id=zone_id, generated_turn=generated_turn,
        )

    def get_unintegrated_bg_content(self, content_type: str | None = None) -> list[dict[str, Any]]:
        from persistence.repos import ai_content_repo
        assert self._conn
        return ai_content_repo.get_unintegrated_bg_content(self._conn, content_type=content_type)

    def mark_bg_content_integrated(self, row_id: int) -> None:
        from persistence.repos import ai_content_repo
        assert self._conn
        ai_content_repo.mark_bg_content_integrated(self._conn, row_id)

    def store_ai_skill(self, skill_id: str, definition: dict, source: str, generated_turn: int) -> None:
        from persistence.repos import ai_content_repo
        assert self._conn
        ai_content_repo.store_ai_skill(self._conn, skill_id, definition, source, generated_turn)

    def load_ai_skills(self) -> list[dict]:
        from persistence.repos import ai_content_repo
        assert self._conn
        return ai_content_repo.load_ai_skills(self._conn)

    def store_world_event(
        self, event_type: str, event_text: str, zone_id: str | None = None,
        title: str = "", npc_hint: str = "", generated_turn: int = 0,
    ) -> None:
        from persistence.repos import ai_content_repo
        assert self._conn
        ai_content_repo.store_world_event(
            self._conn, event_type, event_text, zone_id=zone_id,
            title=title, npc_hint=npc_hint, generated_turn=generated_turn,
        )

    def get_unshown_events(self, limit: int = 10) -> list[dict[str, Any]]:
        from persistence.repos import ai_content_repo
        assert self._conn
        return ai_content_repo.get_unshown_events(self._conn, limit=limit)

    def mark_events_shown(self, ids: list[int]) -> None:
        from persistence.repos import ai_content_repo
        assert self._conn
        ai_content_repo.mark_events_shown(self._conn, ids)

    def get_recent_events(self, limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
        from persistence.repos import ai_content_repo
        assert self._conn
        return ai_content_repo.get_recent_events(self._conn, limit=limit, event_type=event_type)

    # ── Bot instances (delegates to persistence/repos/bot_repo.py) ───────────

    def upsert_bot_instance(self, bot_id: str, definition: dict, current_zone_id: str, last_active_turn: int) -> None:
        from persistence.repos import bot_repo
        assert self._conn
        bot_repo.upsert_bot_instance(self._conn, bot_id, definition, current_zone_id, last_active_turn)

    def load_bot_instances(self) -> list[dict]:
        from persistence.repos import bot_repo
        assert self._conn
        return bot_repo.load_bot_instances(self._conn)

    # ── Guild (state + members + relations + projects) → guild_db_repo.py ────

    def add_guild_state(
        self, guild_id: str, template_id: str, name: str, founding_turn: int,
        headquarters_zone_id: str, archetype: str,
        founder_entity_id: str = "", founding_reason: str = "",
        is_player_founded: int = 0,
    ) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.add_guild_state(
            self._conn, guild_id, template_id, name, founding_turn,
            headquarters_zone_id, archetype,
            founder_entity_id=founder_entity_id, founding_reason=founding_reason,
            is_player_founded=is_player_founded,
        )

    def get_guild_state(self, guild_id: str) -> dict | None:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_guild_state(self._conn, guild_id)

    def get_all_guild_states(self) -> list[dict]:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_all_guild_states(self._conn)

    def update_guild_state(self, guild_id: str, **fields) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.update_guild_state(self._conn, guild_id, **fields)

    def add_guild_member(
        self, guild_id: str, entity_id: str, entity_type: str,
        rank_id: str, joined_turn: int,
        loyalty: int = 50, ambition: int = 50,
    ) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.add_guild_member(
            self._conn, guild_id, entity_id, entity_type,
            rank_id, joined_turn, loyalty=loyalty, ambition=ambition,
        )

    def get_guild_members(self, guild_id: str) -> list[dict]:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_guild_members(self._conn, guild_id)

    def get_member(self, guild_id: str, entity_id: str) -> dict | None:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_member(self._conn, guild_id, entity_id)

    def update_member(self, guild_id: str, entity_id: str, **fields) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.update_member(self._conn, guild_id, entity_id, **fields)

    def remove_guild_member(self, guild_id: str, entity_id: str) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.remove_guild_member(self._conn, guild_id, entity_id)

    def set_guild_relation(
        self, guild_id_1: str, guild_id_2: str, stance: str = "neutral",
        trust: int = 50, hostility: int = 0, tension: int = 0, turn: int = 0,
    ) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.set_guild_relation(
            self._conn, guild_id_1, guild_id_2, stance=stance,
            trust=trust, hostility=hostility, tension=tension, turn=turn,
        )

    def get_guild_relation(self, guild_id_1: str, guild_id_2: str) -> dict | None:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_guild_relation(self._conn, guild_id_1, guild_id_2)

    def update_guild_relation(self, guild_id_1: str, guild_id_2: str, **fields) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.update_guild_relation(self._conn, guild_id_1, guild_id_2, **fields)

    def add_guild_project(
        self, project_id: str, guild_id: str, project_type: str,
        started_turn: int, target_id: str = "", risk: int = 10,
        lead_entity_id: str = "",
    ) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.add_guild_project(
            self._conn, project_id, guild_id, project_type, started_turn,
            target_id=target_id, risk=risk, lead_entity_id=lead_entity_id,
        )

    def get_guild_projects(self, guild_id: str) -> list[dict]:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_guild_projects(self._conn, guild_id)

    def get_project(self, project_id: str) -> dict | None:
        from persistence.repos import guild_db_repo
        assert self._conn
        return guild_db_repo.get_project(self._conn, project_id)

    def update_project(self, project_id: str, progress: int) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.update_project(self._conn, project_id, progress)

    def complete_project(self, project_id: str) -> None:
        from persistence.repos import guild_db_repo
        assert self._conn
        guild_db_repo.complete_project(self._conn, project_id)
