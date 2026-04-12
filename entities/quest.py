"""
Quest entities — templates loaded from JSON, instances live in SQLite.

QuestStage    — a single step in a quest's progression
QuestTemplate — the full static definition of a quest
QuestRegistry — loads all templates from a JSON file
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QuestStage(BaseModel):
    stage_id: str
    description: str            # internal description
    objective_text: str         # player-facing objective line
    # Completion condition dict — one of:
    #   {"has_item":   "item_id"}
    #   {"has_flag":   "flag_name"}
    #   {"world_flag": "flag_name"}
    completion_condition: dict[str, Any] = Field(default_factory=dict)
    # Triggers fired when this stage completes and advances (same syntax as scene triggers)
    on_advance_triggers: list[str] = Field(default_factory=list)
    next_stage_id: str | None = None    # None = this is the terminal stage


class QuestTemplate(BaseModel):
    template_id: str
    title: str
    description: str
    giver_npc_id: str | None = None     # npc_id of whoever gives this quest
    stages: list[QuestStage]
    reward_gold: int = 0
    reward_items: list[str] = Field(default_factory=list)
    reward_flags: list[str] = Field(default_factory=list)
    reward_xp: int = 0
    alignment_reward: float = 0.0
    # Each dict has same shape as completion_condition; if ANY is met, quest fails
    failure_conditions: list[dict[str, Any]] = Field(default_factory=list)
    time_limit_turns: int | None = None

    def get_stage(self, stage_id: str) -> QuestStage | None:
        for s in self.stages:
            if s.stage_id == stage_id:
                return s
        return None

    @property
    def initial_stage_id(self) -> str:
        return self.stages[0].stage_id if self.stages else "complete"


class QuestRegistry:
    def __init__(self) -> None:
        self._quests: dict[str, QuestTemplate] = {}

    def load_from_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            quest = QuestTemplate.model_validate(entry)
            self._quests[quest.template_id] = quest

    def get(self, template_id: str) -> QuestTemplate | None:
        return self._quests.get(template_id)

    def all(self) -> list[QuestTemplate]:
        return list(self._quests.values())
