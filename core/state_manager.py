from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from entities.player import Player

if TYPE_CHECKING:
    from persistence.world_db import WorldDatabase


class GameState:
    def __init__(self, player: Player, world_db: "WorldDatabase | None" = None) -> None:
        self.player = player
        self.world_db: "WorldDatabase | None" = world_db
        self.current_scene_id: str = "prologue"
        self.current_node_id: str = "root"
        self.is_in_combat: bool = False
        self.turn_number: int = 0
        self.generated_content_cache: dict[str, Any] = {}
        self._dirty: bool = False

    # ── Dirty tracking ────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        self._dirty = True

    def clear_dirty(self) -> None:
        self._dirty = False

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    # ── Turn management ───────────────────────────────────────────────────────

    def advance_turn(self) -> None:
        self.turn_number += 1
        self.player.turn_count += 1

    # ── AI cache (in-memory, also persisted to player.flags for save) ─────────

    def get_ai_cache_key(
        self,
        base_classes: list[str],
        catalyst_items: list[str],
        divergence_flags: list[str],
    ) -> str:
        key_data = json.dumps({
            "classes": sorted(base_classes),
            "catalysts": sorted(catalyst_items),
            "flags": sorted(divergence_flags),
        }, sort_keys=True)
        return hashlib.md5(key_data.encode()).hexdigest()

    def cache_ai_result(self, key: str, result: Any) -> None:
        self.generated_content_cache[key] = result

    def get_cached_ai_result(self, key: str) -> Any | None:
        return self.generated_content_cache.get(key)

    # ── World DB convenience passthrough ──────────────────────────────────────

    def log(self, event_type: str, payload: dict | None = None) -> None:
        """Log a world event if world_db is available."""
        if self.world_db:
            self.world_db.log_event(self.turn_number, event_type, payload)

    def set_world_flag(self, key: str, value: str = "true") -> None:
        if self.world_db:
            self.world_db.set_world_flag(key, value, self.turn_number)

    def has_world_flag(self, key: str) -> bool:
        if self.world_db:
            return self.world_db.has_world_flag(key)
        return False
