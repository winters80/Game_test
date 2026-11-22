"""
Boss lairs — static definitions loaded from data/dungeons/*.json.

A lair is a boss room whose occupant changes each time it is killed: a wizard
dies and comes back as a lich, the priests who come to cleanse the lich's den
are corrupted, and so on. This module only holds the authored content; who is
in each lair right now lives in the world DB (``dungeon_lairs`` /
``lair_history``) and the logic is in systems/lair_system.py.

LairEnemyTemplate   — a boss-room enemy, registered into combat_system.ENEMY_TEMPLATES
LairSuccessor       — one "who moves in next" rule, with optional conditions
LairAftermath       — a choice the player makes over the body
LairOccupant        — one link in the chain: enemies, texts, successors
LairDefinition      — one boss room and its whole chain
LairRegistry        — loads the JSON, validates every reference
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class LairAbility(BaseModel):
    """A boss's signature move (systems/combat_effects.enemy_turn)."""
    name: str
    kind: Literal["heavy", "status", "heal", "summon", "drain"]
    every: int = Field(default=3, ge=2, le=10)
    power: float = Field(default=1.8, ge=0.05, le=3.0)   # heavy: damage ×; heal: share of max HP; drain: share healed
    status: str = ""                                     # status: what it inflicts
    summon: str = ""                                     # summon: enemy template id


class LairEnemyTemplate(BaseModel):
    name: str
    max_hp: int = Field(ge=1, le=2000)
    attack: int = Field(ge=0, le=200)
    defense: int = Field(default=0, ge=0, le=100)
    xp_reward: int = Field(default=10, ge=0, le=5000)
    gold_reward: int = Field(default=0, ge=0, le=100_000)   # copper
    rank: str = "D"
    status_on_hit: tuple[str, float] | None = None
    loot_table: list[tuple[str, float]] = Field(default_factory=list)
    abilities: list[LairAbility] = Field(default_factory=list)
    enrage_below: float = Field(default=0.0, ge=0.0, le=0.9)


class LairSuccessorWhen(BaseModel):
    """All set fields must match for the rule to apply."""
    killed_by: Literal["magic", "physical"] | None = None
    killer: Literal["player", "bot"] | None = None
    aftermath: str | None = None
    alignment_min: float | None = None      # player kills only
    alignment_max: float | None = None


class LairSuccessor(BaseModel):
    to: str
    weight: float = Field(default=1.0, gt=0)
    when: LairSuccessorWhen = Field(default_factory=LairSuccessorWhen)


class LairAftermath(BaseModel):
    id: str
    label: str
    text: str = ""
    triggers: list[str] = Field(default_factory=list)


class LairStudy(BaseModel):
    stat: str = "INT"
    min: int = 12
    text: str = ""


class LairOccupant(BaseModel):
    occupant_id: str = ""
    title: str
    # Empty → inherit the previous occupant's lineage name (the lich keeps
    # the wizard's name). Otherwise a fresh name is drawn in order.
    name_pool: list[str] = Field(default_factory=list)
    enemies: list[str] = Field(min_length=1)
    intro: str
    study: LairStudy | None = None
    victory: str = ""
    # First option is what happens when nobody chooses (bot kills).
    aftermath: list[LairAftermath] = Field(default_factory=list)
    omen: str = ""          # shown in the empty lair while this occupant's successor moves in
    rumour: str = ""        # told when this occupant's successor arrives
    # The same, written from the newcomer's side; win over the dead one's
    # omen / rumour, so a branching chain always hints at what really comes.
    arrival_omen: str = ""
    arrival_rumour: str = ""
    trophy: str = ""        # item id given the first time the player kills this occupant
    successors: list[LairSuccessor] = Field(default_factory=list)

    @property
    def default_aftermath(self) -> str:
        return self.aftermath[0].id if self.aftermath else ""


class LairDefinition(BaseModel):
    lair_id: str
    name: str
    zone_id: str
    min_level: int = 1
    respawn_turns: int = Field(default=30, ge=1)
    first_occupant: str
    clear_flags: list[str] = Field(default_factory=list)
    fallback: list[str] = Field(default_factory=list)
    occupants: dict[str, LairOccupant]

    @model_validator(mode="after")
    def _check_refs(self) -> "LairDefinition":
        for oid, occ in self.occupants.items():
            occ.occupant_id = oid
        known = set(self.occupants)
        refs = [self.first_occupant, *self.fallback]
        refs += [s.to for o in self.occupants.values() for s in o.successors]
        missing = sorted({r for r in refs if r not in known})
        if missing:
            raise ValueError(f"lair {self.lair_id}: unknown occupants {missing}")
        if not self.fallback:
            self.fallback = [self.first_occupant]
        return self

    def occupant(self, occupant_id: str) -> LairOccupant | None:
        return self.occupants.get(occupant_id)


class LairRegistry:
    def __init__(self) -> None:
        self._lairs: dict[str, LairDefinition] = {}
        self.enemy_templates: dict[str, LairEnemyTemplate] = {}

    def load_from_dir(self, directory: Path) -> None:
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.json")):
            self.load_from_file(path)

    def load_from_file(self, path: Path) -> None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for eid, raw in (data.get("enemy_templates") or {}).items():
            self.enemy_templates[eid] = LairEnemyTemplate.model_validate(raw)
        for raw in data.get("lairs", []):
            lair = LairDefinition.model_validate(raw)
            self._lairs[lair.lair_id] = lair

    def register(self, lair: LairDefinition) -> None:
        self._lairs[lair.lair_id] = lair

    def unregister(self, lair_id: str) -> None:
        self._lairs.pop(lair_id, None)

    def get(self, lair_id: str) -> LairDefinition | None:
        return self._lairs.get(lair_id)

    def all(self) -> list[LairDefinition]:
        return list(self._lairs.values())

    def in_zone(self, zone_id: str) -> list[LairDefinition]:
        return [l for l in self._lairs.values() if l.zone_id == zone_id]
