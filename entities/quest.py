"""
Quest entities — templates loaded from JSON, instances live in SQLite.

QuestStage    — a single step in a quest's progression
QuestTemplate — the full static definition of a quest
QuestRegistry — loads all templates from a JSON file
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Hard ceiling on quest gold rewards (denominated in gold pieces, NOT copper).
# Anything above this almost certainly means an author typed copper by mistake
# — the player wallet stores copper, so 50,000 in the JSON would land as
# 5,000,000 copper = 50,000 gold, more than most playthroughs ever see.
MAX_QUEST_REWARD_GOLD = 5000


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
    # ── Reward gold is denominated in GOLD PIECES (not copper). ──────────────
    # The player wallet (player.gold) stores copper; quest_system multiplies by
    # COPPER_PER_GOLD when applying rewards. A value of 50 here means the
    # player ends up with +5,000 copper = +50 gold. The validator below
    # enforces a sane ceiling so a content author can't accidentally type
    # copper (e.g. `reward_gold: 5000` → 500,000 copper = 5,000 gold) without
    # tripping a loud error at load time.
    reward_gold: int = Field(
        default=0, ge=0, le=MAX_QUEST_REWARD_GOLD,
        description=(
            "Quest gold reward in GOLD PIECES (not copper). The engine "
            "multiplies by COPPER_PER_GOLD when applied to the player wallet."
        ),
    )
    reward_items: list[str] = Field(default_factory=list)
    reward_flags: list[str] = Field(default_factory=list)
    reward_xp: int = 0
    alignment_reward: float = 0.0
    faction_rewards: dict[str, float] = Field(default_factory=dict)   # {faction_id: delta}
    guild_rewards: dict[str, float] = Field(default_factory=dict)     # {guild_id: delta}
    # Each dict has same shape as completion_condition; if ANY is met, quest fails
    failure_conditions: list[dict[str, Any]] = Field(default_factory=list)
    time_limit_turns: int | None = None

    @field_validator("reward_gold", mode="before")
    @classmethod
    def _warn_on_likely_copper(cls, v):
        """
        Pydantic ge=/le= already hard-fails on > MAX_QUEST_REWARD_GOLD, but
        log a clearer warning for the most common author mistake: typing the
        copper amount (e.g. 100c = 1 gold = 100 copper) and getting a quest
        that pays out 100 gold = 10,000 copper.
        """
        try:
            v_int = int(v)
        except (TypeError, ValueError):
            return v
        if v_int > 2500:
            logger.warning(
                f"Quest template reward_gold={v_int} is unusually large. "
                f"Remember: this is denominated in GOLD PIECES, not copper "
                f"(the player wallet stores copper, and the engine multiplies "
                f"by {100} on apply). Cap is {MAX_QUEST_REWARD_GOLD}."
            )
        return v_int

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

    def register(self, template: "QuestTemplate") -> None:
        """Register a dynamically generated quest template at runtime."""
        self._quests[template.template_id] = template
