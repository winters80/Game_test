"""
Boss succession: who holds each boss lair, and who moves in after a kill.

The content (chains, texts, enemies) is in data/dungeons/*.json
(entities/lair.py). This module is the pure logic:

- ``initial_state``   — a fresh lair, its first occupant in residence
- ``spawn_occupant``  — the current boss as combat Enemies, named and scaled
- ``record_kill``     — the boss died: pick the successor from how it died,
                        leave the lair empty until ``respawn_turns`` pass
- ``tick``            — the successor moves in once its time comes

Each time the same occupant takes the lair again it is a little stronger
(``LAIR_SCALING_PER_RETURN``, capped at ``LAIR_MAX_SCALING``): the dungeon
learns.

No I/O and no rendering; core/lair_flow.py does the engine side and the
world DB stores ``LairState`` as JSON.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from config import LAIR_MAX_SCALING, LAIR_SCALING_PER_RETURN

if TYPE_CHECKING:
    from entities.lair import LairDefinition, LairOccupant, LairRegistry
    from systems.combat_system import Enemy

logger = logging.getLogger(__name__)

MAGIC_STATS = {"INT", "WIS"}
_REGISTERED: set[str] = set()     # enemy ids this module added


class LairState(BaseModel):
    lair_id: str
    occupant_id: str
    lineage_name: str = ""
    generation: int = 0                       # 0 = the first occupant
    status: Literal["occupied", "vacant"] = "occupied"
    next_occupant_id: str = ""
    next_lineage_name: str = ""
    arrives_turn: int = 0
    held_counts: dict[str, int] = Field(default_factory=dict)   # times each occupant has held the lair
    kills: int = 0

    @property
    def occupied(self) -> bool:
        return self.status == "occupied"


@dataclass
class KillInfo:
    killer_kind: Literal["player", "bot"]
    killer_name: str
    killer_level: int = 1
    killed_by: Literal["magic", "physical"] = "physical"
    aftermath: str = ""
    alignment: float | None = None            # the player's, for player kills


# ── Setup ────────────────────────────────────────────────────────────────────

def register_enemy_templates(registry: "LairRegistry") -> int:
    """Add lair enemies to combat_system.ENEMY_TEMPLATES. Never overwrites
    a template that already exists. Returns how many were added."""
    from systems.combat_system import ENEMY_TEMPLATES

    added = 0
    for eid, tmpl in registry.enemy_templates.items():
        if eid in ENEMY_TEMPLATES and eid not in _REGISTERED:
            logger.warning("Lair enemy %s clashes with an existing enemy; skipped", eid)
            continue
        data = tmpl.model_dump()
        data.update(enemy_id=eid, current_hp=tmpl.max_hp)
        ENEMY_TEMPLATES[eid] = data
        _REGISTERED.add(eid)
        added += 1
    return added


def pick_name(occ: "LairOccupant", state: LairState | None) -> str:
    """A fresh name from the occupant's pool (in order, by how often it has
    held the lair), else the lineage name it inherits."""
    if occ.name_pool:
        times = state.held_counts.get(occ.occupant_id, 0) if state else 0
        return occ.name_pool[times % len(occ.name_pool)]
    return state.lineage_name if state else ""


def initial_state(lair: "LairDefinition") -> LairState:
    first = lair.occupants[lair.first_occupant]
    return LairState(
        lair_id=lair.lair_id, occupant_id=first.occupant_id,
        lineage_name=pick_name(first, None), held_counts={first.occupant_id: 1},
    )


def fmt(text: str, name: str) -> str:
    return text.replace("{name}", name or "the nameless one")


# ── The boss ─────────────────────────────────────────────────────────────────

def scaling(state: LairState, occupant_id: str | None = None) -> float:
    """Stat multiplier: +LAIR_SCALING_PER_RETURN each time this occupant
    has held the lair before, capped."""
    held = state.held_counts.get(occupant_id or state.occupant_id, 1)
    return 1.0 + min(LAIR_MAX_SCALING, LAIR_SCALING_PER_RETURN * max(0, held - 1))


def spawn_occupant(lair: "LairDefinition", state: LairState) -> list["Enemy"]:
    from systems.combat_system import spawn_enemy

    occ = lair.occupant(state.occupant_id)
    if occ is None or not state.occupied:
        return []
    mult = scaling(state)
    enemies = []
    for eid in occ.enemies:
        e = spawn_enemy(eid)
        if e is None:
            logger.warning("Lair %s: unknown enemy %s", lair.lair_id, eid)
            continue
        e.name = fmt(e.name, state.lineage_name)
        if mult > 1.0:
            e.max_hp = e.current_hp = round(e.max_hp * mult)
            e.attack = round(e.attack * mult)
            e.xp_reward = round(e.xp_reward * mult)
            e.gold_reward = round(e.gold_reward * mult)
        enemies.append(e)
    return enemies


def occupant_title(lair: "LairDefinition", state: LairState, next_one: bool = False) -> str:
    oid = state.next_occupant_id if next_one else state.occupant_id
    name = state.next_lineage_name if next_one else state.lineage_name
    occ = lair.occupant(oid)
    return fmt(occ.title, name) if occ else "something"


# ── Succession ───────────────────────────────────────────────────────────────

def _matches(when, kill: KillInfo) -> bool:
    if when.killed_by and when.killed_by != kill.killed_by:
        return False
    if when.killer and when.killer != kill.killer_kind:
        return False
    if when.aftermath and when.aftermath != kill.aftermath:
        return False
    if when.alignment_min is not None and (kill.alignment is None or kill.alignment < when.alignment_min):
        return False
    if when.alignment_max is not None and (kill.alignment is None or kill.alignment > when.alignment_max):
        return False
    return True


def choose_successor(lair: "LairDefinition", occ: "LairOccupant", kill: KillInfo,
                     rng: random.Random) -> str:
    rules = [s for s in occ.successors if _matches(s.when, kill)]
    if not rules:
        return rng.choice(lair.fallback)
    total = sum(r.weight for r in rules)
    roll = rng.uniform(0, total)
    for r in rules:
        roll -= r.weight
        if roll <= 0:
            return r.to
    return rules[-1].to


def record_kill(lair: "LairDefinition", state: LairState, kill: KillInfo, turn: int,
                rng: random.Random | None = None) -> dict:
    """The boss is dead. Mutates ``state`` (vacant, successor chosen) and
    returns the history row to store."""
    occ = lair.occupants[state.occupant_id]
    if not kill.aftermath:
        kill.aftermath = occ.default_aftermath
    rng = rng or random.Random(f"{lair.lair_id}:{state.generation}:{turn}")
    nxt = choose_successor(lair, occ, kill, rng)
    nxt_occ = lair.occupants[nxt]
    row = {
        "lair_id": lair.lair_id, "generation": state.generation,
        "occupant_id": state.occupant_id, "occupant_name": occupant_title(lair, state),
        "killer_kind": kill.killer_kind, "killer_name": kill.killer_name,
        "killer_level": kill.killer_level, "killed_by": kill.killed_by,
        "aftermath": kill.aftermath, "successor_id": nxt, "zone_id": lair.zone_id, "turn": turn,
    }
    state.status = "vacant"
    state.kills += 1
    state.next_occupant_id = nxt
    state.next_lineage_name = pick_name(nxt_occ, state)
    state.arrives_turn = turn + lair.respawn_turns
    return row


def tick(lair: "LairDefinition", state: LairState, turn: int) -> bool:
    """Move the successor in if its time has come. Returns True on arrival."""
    if state.occupied or turn < state.arrives_turn or state.next_occupant_id not in lair.occupants:
        return False
    oid = state.next_occupant_id
    state.occupant_id = oid
    state.lineage_name = state.next_lineage_name
    state.generation += 1
    state.held_counts[oid] = state.held_counts.get(oid, 0) + 1
    state.status = "occupied"
    state.next_occupant_id = state.next_lineage_name = ""
    return True


def omen_text(lair: "LairDefinition", state: LairState) -> str:
    """What an empty lair looks like: the dead occupant's omen of what's coming."""
    occ = lair.occupant(state.occupant_id)
    return fmt(occ.omen, state.lineage_name) if occ and occ.omen else (
        f"{lair.name[0].upper()}{lair.name[1:]} stands empty. It won't stay that way.")


def rumour_text(lair: "LairDefinition", previous_occupant_id: str, previous_name: str) -> str:
    occ = lair.occupant(previous_occupant_id)
    return fmt(occ.rumour, previous_name) if occ and occ.rumour else ""


def killing_blow_kind(skill) -> Literal["magic", "physical"]:
    """Classify a finishing blow: a spell (spell_type, MP cost, or INT/WIS
    scaling) is magic; basic attacks and martial skills are physical."""
    if skill is None:
        return "physical"
    if getattr(skill, "spell_type", "") or getattr(skill, "mp_cost", 0) > 0:
        return "magic"
    if any((e.scaling_stat or "").upper() in MAGIC_STATS for e in getattr(skill, "effects", [])):
        return "magic"
    return "physical"
