from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import SAVE_VERSION
from entities.player import Player
from core.state_manager import GameState
from persistence.world_db import WorldDatabase

logger = logging.getLogger(__name__)


def save_game(state: GameState, slot_name: str, saves_dir: Path) -> Path:
    saves_dir.mkdir(exist_ok=True)

    # ── Player JSON save ───────────────────────────────────────────────────────
    save_data = {
        "save_version": SAVE_VERSION,
        "player": state.player.model_dump(mode="json"),
        "current_scene_id": state.current_scene_id,
        "current_node_id": state.current_node_id,
        "turn_number": state.turn_number,
        "generated_content_cache": state.generated_content_cache,
    }
    json_path = saves_dir / f"{slot_name}.json"
    json_path.write_text(json.dumps(save_data, indent=2, default=str), encoding="utf-8")

    # ── World DB is already open and auto-persists (WAL mode) ─────────────────
    # The .db file lives at saves/{slot_name}.db and is written to live.
    # Nothing extra needed here — SQLite commits happen per-operation.

    state.clear_dirty()
    return json_path


def load_game(slot_name: str, saves_dir: Path) -> GameState | None:
    json_path = saves_dir / f"{slot_name}.json"
    if not json_path.exists():
        return None

    raw: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))

    # ── Migrate old saves to current schema ───────────────────────────────────
    file_version = raw.get("save_version", 1)
    if file_version > SAVE_VERSION:
        raise SaveMigrationError(
            f"Save file '{slot_name}.json' is from a newer game version "
            f"(v{file_version} > v{SAVE_VERSION}). Refusing to load — "
            "downgrade is not supported."
        )
    if file_version < SAVE_VERSION:
        raw = _migrate(raw, file_version, SAVE_VERSION)

    player = Player.model_validate(raw["player"])

    # ── Open paired world DB ──────────────────────────────────────────────────
    db_path = saves_dir / f"{slot_name}.db"
    world_db = WorldDatabase(db_path)
    world_db.open()

    state = GameState(player, world_db=world_db)
    # Stash AI-generated skill definitions for GameEngine to re-register
    if world_db is not None:
        state._ai_skill_defs = world_db.load_ai_skills()
    state.current_scene_id = raw.get("current_scene_id", "village_start")
    state.current_node_id = raw.get("current_node_id", "root")
    state.turn_number = raw.get("turn_number", 0)
    state.generated_content_cache = raw.get("generated_content_cache", {})
    return state


def new_game_state(player: Player, saves_dir: Path, slot_name: str) -> GameState:
    """Create a brand-new GameState with a fresh world database."""
    saves_dir.mkdir(exist_ok=True)
    db_path = saves_dir / f"{slot_name}.db"
    world_db = WorldDatabase(db_path)
    world_db.open()
    state = GameState(player, world_db=world_db)
    return state


def list_saves(saves_dir: Path) -> list[str]:
    if not saves_dir.exists():
        return []
    # Only show slots that have both a .json AND (optionally) a .db
    return sorted(p.stem for p in saves_dir.glob("*.json") if p.stem != ".gitkeep")


def close_game(state: GameState) -> None:
    """Clean up: close the world DB connection."""
    if state.world_db:
        state.world_db.close()


# ── Migrations ────────────────────────────────────────────────────────────────


class SaveMigrationError(Exception):
    """Raised when a save file cannot be safely migrated to the current schema."""


def _migrate(data: dict[str, Any], from_version: int, to_version: int) -> dict[str, Any]:
    """
    Apply migrations sequentially from ``from_version`` up to ``to_version``.

    Each step is a small, explicit function (``_vN_to_vN+1``). After all
    migrations run, the resulting player blob is validated against the current
    Player model — this surfaces silent schema drift early (e.g. someone added
    a required-no-default field without also defining its migrator).
    """
    logger.info(f"Migrating save from v{from_version} to v{to_version}")

    migrators = {
        1: _v1_to_v2,
        2: _v2_to_v3,
    }

    v = from_version
    while v < to_version:
        migrator = migrators.get(v)
        if migrator is None:
            raise SaveMigrationError(
                f"No migrator defined for save_version {v} → {v + 1}. "
                "Add a _v{v}_to_v{v+1}() function in persistence/save_manager.py."
            )
        try:
            data = migrator(data)
        except Exception as e:
            raise SaveMigrationError(
                f"Migration v{v} → v{v + 1} failed: {e}"
            ) from e
        v += 1

    # Final guard: confirm the migrated player blob matches the current model.
    # If this raises, a field was added to Player without a default AND no
    # migrator was written to fill it in — the loudest possible signal.
    try:
        Player.model_validate(data["player"])
    except Exception as e:
        raise SaveMigrationError(
            f"Migrated save (v{to_version}) does not validate against the "
            f"current Player schema. A field was likely added without a default "
            f"or a migration was skipped. Details: {e}"
        ) from e

    return data


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """
    v1 → v2: Player gained species_id, gender, alignment, lives_remaining/used,
    guild_memberships, faction_standing_cache, active/completed_quest_ids,
    evolution_stage, background_id, perception_bonus, last_safe_zone_id; and
    the top-level dict gained turn_number plus a paired SQLite .db file.

    Every new player field has a default on the Pydantic model, so Pydantic
    will fill them in automatically. We still set them explicitly here so the
    file on disk after migration is self-describing.
    """
    data["save_version"] = 2

    if "turn_number" not in data:
        data["turn_number"] = 0

    player = data.get("player", {})
    player.setdefault("species_id", "human")
    player.setdefault("gender", "unspecified")
    player.setdefault("alignment", 0.0)
    player.setdefault("lives_remaining", 9)
    player.setdefault("lives_used", 0)
    player.setdefault("guild_memberships", {})
    player.setdefault("faction_standing_cache", {})
    player.setdefault("active_quest_ids", [])
    player.setdefault("completed_quest_ids", [])
    player.setdefault("evolution_stage", 0)
    player.setdefault("background_id", None)
    player.setdefault("perception_bonus", 0)
    player.setdefault("last_safe_zone_id", "village_start")
    data["player"] = player
    return data


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """
    v2 → v3: Player gained identified_items, background_narrative,
    active_buffs, skill_cooldowns, play_time_seconds; the SQLite world_db
    schema also advanced (see DB_SCHEMA_VERSION in persistence/world_db.py).

    All new player fields have safe defaults. We set them explicitly so that
    a re-saved file is self-describing and not relying on Pydantic to refill
    them on every load.
    """
    data["save_version"] = 3

    player = data.get("player", {})
    player.setdefault("identified_items", {})
    player.setdefault("background_narrative", "")
    player.setdefault("active_buffs", [])
    player.setdefault("skill_cooldowns", {})
    player.setdefault("play_time_seconds", 0)
    data["player"] = player
    return data
