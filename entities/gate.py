"""
Gates — instanced dungeons loaded from data/dungeons/gates/*.json.

Since the Fracture, rifts ("Gates") open all over Aethoria and the System
registers each one. A gate is a short run: a few rooms (fights, traps,
treasure), then a boss lair. The lair uses the same succession rules as the
fixed dungeon lairs (entities/lair.py): kill its master and something else
moves in.

GateRoom        — one room: fight | trap | treasure
GateDefinition  — the gate, its rooms, and its lair (built as a LairDefinition)
GateRegistry    — loads gate files; registers each gate's lair and enemies
                  into the LairRegistry so lair_flow / lair_system handle them
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from entities.lair import LairDefinition, LairEnemyTemplate

if TYPE_CHECKING:
    from entities.lair import LairRegistry

logger = logging.getLogger(__name__)

GATE_ZONE_PREFIX = "gate:"


class GateCheck(BaseModel):
    stat: str = "AGI"
    min: int = 10
    success: str = ""
    fail: str = ""
    damage_pct: float = Field(default=0.15, ge=0, le=0.5)   # of max HP, on failure


class GateRoom(BaseModel):
    kind: Literal["fight", "trap", "treasure"] = "fight"
    text: str
    enemies: list[str] = Field(default_factory=list)          # fight
    check: GateCheck | None = None                            # trap (and optional for treasure)
    loot: list[tuple[str, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "GateRoom":
        if self.kind == "fight" and not self.enemies:
            raise ValueError("fight room needs enemies")
        if self.kind == "trap" and self.check is None:
            raise ValueError("trap room needs a check")
        return self


class GateDefinition(BaseModel):
    gate_id: str
    name: str
    theme: str                      # mythical | technology | paranormal | elemental | ...
    min_level: int = Field(ge=1, le=50)
    region: str = "verath_city"     # where the gate is registered (flavour + bots)
    description: str
    entry_text: str
    rooms: list[GateRoom] = Field(min_length=1)
    material: str = ""              # signature material the gate drops (quests ask for it)
    lair: LairDefinition

    @model_validator(mode="before")
    @classmethod
    def _build_lair(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("lair"), dict):
            lair = dict(data["lair"])
            lair.setdefault("lair_id", data.get("gate_id"))
            lair.setdefault("name", f"the heart of {data.get('name', 'the gate')}")
            lair.setdefault("zone_id", GATE_ZONE_PREFIX + str(data.get("gate_id")))
            lair.setdefault("min_level", data.get("min_level", 1))
            data = {**data, "lair": lair}
        return data

    @property
    def clear_flag(self) -> str:
        return f"cleared_{self.gate_id}"


class GateRegistry:
    def __init__(self) -> None:
        self._gates: dict[str, GateDefinition] = {}

    def load_from_dir(self, directory: Path, lair_registry: "LairRegistry") -> None:
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.json")):
            self.load_from_file(path, lair_registry)

    def load_from_file(self, path: Path, lair_registry: "LairRegistry") -> None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for eid, raw in (data.get("enemy_templates") or {}).items():
            lair_registry.enemy_templates[eid] = LairEnemyTemplate.model_validate(raw)
        for raw in data.get("gates", []):
            gate = GateDefinition.model_validate(raw)
            if gate.gate_id in self._gates:
                logger.warning("Duplicate gate %s in %s; later one wins", gate.gate_id, path.name)
            self._gates[gate.gate_id] = gate
            lair_registry.register(gate.lair)

    def register(self, gate: GateDefinition, lair_registry: "LairRegistry") -> None:
        self._gates[gate.gate_id] = gate
        lair_registry.register(gate.lair)

    def get(self, gate_id: str) -> GateDefinition | None:
        return self._gates.get(gate_id)

    def all(self) -> list[GateDefinition]:
        return sorted(self._gates.values(), key=lambda g: (g.min_level, g.name))
