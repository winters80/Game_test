-- Migration 004: Add guild runtime state tables
-- Applied when DB_SCHEMA_VERSION < 5
-- See persistence/world_db.py WorldDatabase._migrate() for actual migration logic.

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
