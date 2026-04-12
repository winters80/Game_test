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

def _migrate(data: dict[str, Any], from_version: int, to_version: int) -> dict[str, Any]:
    logger.info(f"Migrating save from v{from_version} to v{to_version}")
    if from_version == 1 and to_version >= 2:
        data = _v1_to_v2(data)
    if from_version < 3:
        data["save_version"] = 3
    return data


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """
    v1 → v2: Add species, alignment, lives, guild, faction fields to player.
    All new fields have defaults in the Pydantic model so model_validate handles them.
    We only need to bump the version number.
    """
    data["save_version"] = 2
    # Ensure turn_number exists (wasn't in v1)
    if "turn_number" not in data:
        data["turn_number"] = 0
    return data
