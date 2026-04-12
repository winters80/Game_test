from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scenes.scene_base import Scene


class SceneRegistry:
    def __init__(self) -> None:
        self._scenes: dict[str, Scene] = {}

    def load_from_dir(self, scenes_dir: Path) -> None:
        for path in scenes_dir.glob("*.json"):
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            scene_id = data.get("scene_id", path.stem)
            scene = Scene(scene_id, data)
            self._scenes[scene_id] = scene

    def register(self, scene: Scene) -> None:
        self._scenes[scene.scene_id] = scene

    def get(self, scene_id: str) -> Scene | None:
        return self._scenes.get(scene_id)

    def all_ids(self) -> list[str]:
        return list(self._scenes.keys())
