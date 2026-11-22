"""
The world grows new dungeons (logic: systems/gate_generation.py).

New Gates open three ways:
- **Rift surges**: every ``RIFT_SURGE_EVERY_TURNS`` a new rift opens, up to
  ``RIFT_MAX_GENERATED`` per save. With the AI online the primary model
  invents it (``new_gate`` background task); offline, an **echo** opens: an
  existing Gate reopened deeper (6-10 levels higher, new ids, the same story).
- **Quests**: the ``open_rift:[+]N`` trigger (special quest lines such as
  Mira's "Uncharted Rifts") opens one at level N, or the player's level + N.
- **Lair successors**: when a master dies, the AI may (``AI_SUCCESSOR_CHANCE``)
  invent who moves in next; it slots in front of the planned successor.

Everything is stored in ``generated_dungeons`` and re-registered per save by
``reload_generated_dungeons`` (called from core/lair_flow.setup_lairs).
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from config import (
    AI_SUCCESSOR_CHANCE, RIFT_MAX_GENERATED, RIFT_SURGE_EVERY_TURNS, feature,
)
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)

THEMES = ["mythical", "technology", "paranormal", "elemental", "cosmic", "eldritch", "celestial", "nature", "undead"]


def _db(engine: "GameEngine"):
    return engine.state.world_db if feature("world_db") and engine.state is not None else None


def generated_gates(engine: "GameEngine") -> list:
    return [g for g in engine.gate_registry.all() if g.gate_id.startswith("gen_")] if engine.gate_registry else []


# ── Registering content ──────────────────────────────────────────────────────

def _register_fragment(engine: "GameEngine", frag: dict) -> "object | None":
    from entities.gate import GateDefinition
    from entities.item import Item
    from entities.lair import LairEnemyTemplate
    from systems.lair_system import register_enemy_templates

    for eid, raw in frag.get("enemy_templates", {}).items():
        engine.lair_registry.enemy_templates[eid] = LairEnemyTemplate.model_validate(raw)
    register_enemy_templates(engine.lair_registry)
    mat = frag.get("material")
    if mat and engine.item_registry.get(mat["item_id"]) is None:
        engine.item_registry.register(Item.model_validate(mat))
    if "gate" in frag:
        gate = GateDefinition.model_validate(frag["gate"])
        engine.gate_registry.register(gate, engine.lair_registry)
        return gate
    return None


def _register_successor(engine: "GameEngine", lair, payload: dict) -> str:
    from entities.lair import LairEnemyTemplate, LairOccupant
    from systems.lair_system import register_enemy_templates

    for eid, raw in payload.get("enemy_templates", {}).items():
        engine.lair_registry.enemy_templates[eid] = LairEnemyTemplate.model_validate(raw)
    register_enemy_templates(engine.lair_registry)
    occ = LairOccupant.model_validate(payload["occupant"])
    occ.occupant_id = payload["occupant_id"]
    lair.occupants[occ.occupant_id] = occ
    return occ.occupant_id


def clear_generated(engine: "GameEngine") -> None:
    """Drop every generated Gate and successor (another save's)."""
    from systems.combat_system import ENEMY_TEMPLATES
    from systems.lair_system import _REGISTERED

    if engine.gate_registry is not None:
        for gate in generated_gates(engine):
            engine.gate_registry.unregister(gate.gate_id, engine.lair_registry)
    if engine.lair_registry is not None:
        for lair in engine.lair_registry.all():
            for oid in [o for o in lair.occupants if o.startswith("gen_")]:
                del lair.occupants[oid]
        for eid in [e for e in engine.lair_registry.enemy_templates if e.startswith("gen_")]:
            del engine.lair_registry.enemy_templates[eid]
    for eid in [e for e in ENEMY_TEMPLATES if e.startswith("gen_") and e in _REGISTERED]:
        del ENEMY_TEMPLATES[eid]
        _REGISTERED.discard(eid)


def reload_generated_dungeons(engine: "GameEngine") -> int:
    """Per save: clear other saves' generated dungeons, register this save's."""
    if not feature("boss_succession") or engine.gate_registry is None:
        return 0
    clear_generated(engine)
    db = _db(engine)
    if db is None:
        return 0
    n = 0
    for row in db.load_generated_dungeons():
        try:
            if row["kind"] == "gate":
                _register_fragment(engine, row["payload"])
            elif row["kind"] == "occupant":
                lair = engine.lair_registry.get(row["lair_id"])
                if lair is not None:
                    _register_successor(engine, lair, row["payload"])
            n += 1
        except Exception:
            logger.warning("Could not restore generated dungeon %s", row.get("content_id"), exc_info=True)
    return n


# ── New Gates ────────────────────────────────────────────────────────────────

def open_gate(engine: "GameEngine", frag: dict, source: str, announce: bool = True):
    """Register, store and announce a new Gate fragment. Returns the gate."""
    from core.lair_flow import _save
    from systems.lair_system import initial_state

    gate = _register_fragment(engine, frag)
    if gate is None:
        return None
    if gate.gate_id not in engine._lair_states:
        state = initial_state(gate.lair)
        engine._lair_states[gate.gate_id] = state
        _save(engine, state)
    db = _db(engine)
    turn = engine.state.player.turn_count
    if db is not None:
        db.store_generated_dungeon(gate.gate_id, "gate", frag, turn=turn)
        db.store_world_event("lair", f"A new rift has opened: {gate.name}, rated level {gate.min_level}. "
                                     f"The Gate Hall has registered it.", title="Rift Surge", generated_turn=turn)
    engine.state.player.set_flag("_rifts_opened", int(engine.state.player.flags.get("_rifts_opened", 0) or 0) + 1)
    if announce:
        renderer.print_system_message(
            f"✦ A NEW RIFT OPENS · {gate.name.upper()} · LEVEL {gate.min_level} ({source})", style="system_msg")
    logger.info("Gate opened (%s): %s L%d", source, gate.gate_id, gate.min_level)
    return gate


def _seq(engine: "GameEngine") -> int:
    return len(generated_gates(engine)) + 1


def make_echo(engine: "GameEngine", level: int, rng: random.Random) -> dict | None:
    """An existing authored Gate reopened at ``level`` (no AI needed)."""
    from ai.response_validator import AIGateMaster, AIGateResponse
    from systems.gate_generation import build_gate

    authored = [g for g in engine.gate_registry.all() if not g.gate_id.startswith("gen_")]
    echoed = {g.gate_id.split("gen_echo_", 1)[1].rsplit("_", 1)[0] for g in generated_gates(engine)
              if g.gate_id.startswith("gen_echo_")}
    fresh = [g for g in authored if g.gate_id not in echoed] or authored
    # Prefer a Gate 6-10 levels below the echo's level; nearest otherwise.
    near = [g for g in fresh if 6 <= level - g.min_level <= 10]
    if not near:
        near = sorted(fresh, key=lambda g: abs(level - 8 - g.min_level))[:3]
    if not near:
        return None
    base = rng.choice(near)
    et = engine.lair_registry.enemy_templates
    masters = []
    oid = base.lair.first_occupant
    seen = []
    while oid not in seen and len(seen) < 3:
        seen.append(oid)
        occ = base.lair.occupants[oid]
        boss = et.get(occ.enemies[0])
        moves = [a.name for a in (boss.abilities if boss else [])]
        masters.append(AIGateMaster(
            title=occ.title, boss_name=boss.name if boss else occ.title, names=occ.name_pool,
            intro=occ.intro, victory=occ.victory, omen=occ.arrival_omen, rumour=occ.arrival_rumour,
            status=(boss.status_on_hit[0] if boss and boss.status_on_hit else ""),
            heavy_move=moves[0] if moves else "", second_move=moves[1] if len(moves) > 1 else ""))
        oid = occ.successors[0].to if occ.successors else base.lair.first_occupant
    mobs = []
    for r in base.rooms:
        for e in r.enemies:
            if e in et and et[e].name not in mobs:
                mobs.append(et[e].name)
    resp = AIGateResponse(
        name=f"the Deep Echo of {base.name.removeprefix('the ')}", theme=base.theme,
        description=f"{base.name[0].upper() + base.name[1:]} has opened again, deeper. The System rates the echo at level {level}.",
        entry_text=base.entry_text + " Everything is older here, and hungrier.",
        heart=f"the echoing {base.lair.name.removeprefix('the ')}",
        mob_names=[f"Echoing {m}" for m in mobs[:2]], room_texts=[r.text for r in base.rooms][:3],
        masters=masters if len(masters) >= 2 else masters * 2)
    frag = build_gate(resp, level, _seq(engine), gate_id=f"gen_echo_{base.gate_id}_{_seq(engine)}",
                      party_size=getattr(base, "party_size", 1))
    # An echo drops the original Gate's material, so quests and recipes keep working.
    frag["material"] = None
    for t in frag["enemy_templates"].values():
        t["loot_table"] = [[base.material if i == frag["gate"]["material"] else i, p] for i, p in t["loot_table"]]
    for room in frag["gate"]["rooms"]:
        room["loot"] = [[base.material, p] for _, p in room.get("loot", [])]
    frag["gate"]["material"] = base.material
    return frag


def surge_level(engine: "GameEngine", rng: random.Random, offset: int | None = None) -> int:
    lvl = engine.state.player.level + (offset if offset is not None else rng.randint(1, 4))
    return max(2, min(30, lvl))


def open_rift(engine: "GameEngine", level: int, source: str = "rift surge",
              rng: random.Random | None = None) -> bool:
    """Open a new Gate now (AI if online and queued, else an echo). True if
    something opened or was queued."""
    rng = rng or random.Random(f"rift:{engine.state.player.turn_count}:{_seq(engine)}")
    ai = getattr(engine, "ai_service", None)
    if ai is not None and ai.is_available and getattr(ai, "has_background", False):
        existing = [g.name for g in engine.gate_registry.all()]
        if ai.submit_gate_async(engine.state.player.level, level, existing, THEMES):
            engine._gate_request_turn = engine.state.player.turn_count
            return True
    frag = make_echo(engine, level, rng)
    return frag is not None and open_gate(engine, frag, source) is not None


def tick_gate_growth(engine: "GameEngine") -> None:
    """Per turn: queued quest rifts, and the periodic rift surge."""
    if not feature("boss_succession") or engine.gate_registry is None or engine.state is None:
        return
    state = engine.state
    pending = list(getattr(state, "_pending_rifts", []) or [])
    state._pending_rifts = []
    for spec in pending:
        rel = str(spec).startswith("+")
        try:
            n = int(str(spec).lstrip("+"))
        except ValueError:
            n = 3
        open_rift(engine, surge_level(engine, random.Random(), n) if rel else max(2, min(30, n)), "quest")
    if len(generated_gates(engine)) >= RIFT_MAX_GENERATED:
        return
    turn = state.player.turn_count
    last = int(state.player.flags.get("_last_rift_surge", 0) or 0)
    if turn - last < RIFT_SURGE_EVERY_TURNS:
        return
    if getattr(engine, "_gate_request_turn", None) is not None and turn - engine._gate_request_turn < 60:
        return                                    # an AI gate is still on its way
    state.player.set_flag("_last_rift_surge", turn)
    open_rift(engine, surge_level(engine, random.Random(f"surge:{turn}")))


def apply_gate_result(engine: "GameEngine", result: dict) -> None:
    """Integrate a background ``new_gate`` result."""
    from systems.gate_generation import build_gate

    engine._gate_request_turn = None
    resp = result.get("response")
    if resp is None or engine.gate_registry is None:
        return
    try:
        frag = build_gate(resp, int(result.get("gate_level", 5)), _seq(engine))
        open_gate(engine, frag, "the System detects it")
    except Exception:
        logger.warning("Could not build AI gate", exc_info=True)


# ── AI successors ────────────────────────────────────────────────────────────

def request_successor(engine: "GameEngine", lair, state, kill, rng: random.Random | None = None) -> bool:
    """Maybe ask the AI who moves into a lair whose master just died."""
    ai = getattr(engine, "ai_service", None)
    if ai is None or not ai.is_available or not getattr(ai, "has_background", False):
        return False
    rng = rng or random.Random()
    if rng.random() >= AI_SUCCESSOR_CHANCE:
        return False
    from systems.lair_system import occupant_title
    gate = engine.gate_registry.get(lair.lair_id) if engine.gate_registry else None
    others = [o.title for o in lair.occupants.values()]
    return ai.submit_lair_successor_async(
        lair.lair_id, lair.name, gate.name if gate else lair.name,
        dead_title=occupant_title(lair, state), killed_by=kill.killed_by,
        killer=kill.killer_name, gate_level=lair.min_level, others=others)


def apply_successor_result(engine: "GameEngine", result: dict) -> None:
    from core.lair_flow import _save, get_state
    from systems.gate_generation import build_successor
    from systems.lair_system import pick_name

    lair = engine.lair_registry.get(result.get("lair_id", "")) if engine.lair_registry else None
    state = get_state(engine, result.get("lair_id", ""))
    resp = result.get("response")
    if lair is None or state is None or resp is None or state.occupied:
        return                                     # someone already moved in
    gate = engine.gate_registry.get(lair.lair_id) if engine.gate_registry else None
    seq = sum(1 for o in lair.occupants if o.startswith("gen_")) + 1
    payload = build_successor(resp, lair, lair.min_level, seq, state.next_occupant_id,
                              gate.theme if gate else "mythical")
    oid = _register_successor(engine, lair, payload)
    state.next_occupant_id = oid
    state.next_lineage_name = pick_name(lair.occupants[oid], state)
    _save(engine, state)
    db = _db(engine)
    if db is not None:
        db.store_generated_dungeon(oid, "occupant", payload, lair_id=lair.lair_id,
                                   turn=engine.state.player.turn_count)
