"""
AI Bot Agent system.

BotAgent is a Pydantic model representing an autonomous NPC driven by the
background Ollama generator. BotRegistry loads templates from JSON.
BotManager holds live instances and syncs with world_db.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from entities.player import Stats

if TYPE_CHECKING:
    from persistence.world_db import WorldDatabase


class BotAgent(BaseModel):
    bot_id: str
    name: str
    personality_seed: str = ""
    current_goal: str = "idle"
    current_zone_id: str = "village_start"
    stats: Stats = Field(default_factory=Stats)
    inventory: list[str] = Field(default_factory=list)
    gold: int = 100
    memory: list[str] = Field(default_factory=list)
    disposition: float = 0.0
    turn_last_acted: int = 0


class BotRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, dict] = {}

    def load_from_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            self._templates[entry["bot_id"]] = entry

    def get_template(self, bot_id: str) -> dict | None:
        return self._templates.get(bot_id)

    def all_templates(self) -> list[dict]:
        return list(self._templates.values())


class BotManager:
    def __init__(self) -> None:
        self._bots: list[BotAgent] = []

    def load_from_templates(self, registry: BotRegistry) -> None:
        for tmpl in registry.all_templates():
            self._bots.append(BotAgent.model_validate(tmpl))

    def load_from_db(self, world_db: "WorldDatabase") -> None:
        rows = world_db.load_bot_instances()
        for row in rows:
            agent = BotAgent.model_validate(json.loads(row["definition"]))
            agent.current_zone_id = row["current_zone_id"] or agent.current_zone_id
            self._bots.append(agent)

    def save_to_db(self, world_db: "WorldDatabase") -> None:
        for bot in self._bots:
            world_db.upsert_bot_instance(
                bot_id=bot.bot_id,
                definition=bot.model_dump(mode="json"),
                current_zone_id=bot.current_zone_id,
                last_active_turn=bot.turn_last_acted,
            )

    def active_count(self) -> int:
        return len(self._bots)

    def get(self, bot_id: str) -> BotAgent | None:
        return next((b for b in self._bots if b.bot_id == bot_id), None)

    def all(self) -> list[BotAgent]:
        return list(self._bots)
