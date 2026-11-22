"""
Engine side of Gates (instanced dungeons; content: data/dungeons/gates/*.json,
models: entities/gate.py).

- ``open_gate_registry`` — the ``gate_registry`` trigger: the System's list of
  registered Gates, their level, and who rules each one right now
- ``run_gate``           — the ``enter_gate:GATE_ID`` trigger: the rooms, then
  the boss lair (core/lair_flow.enter_lair), so gate bosses follow the same
  succession rules as the fixed dungeon lairs
- ``bot_gate_view``      — what bots can see of the gates (systems/bot_brain)

Clearing a gate sets ``cleared_<gate_id>`` and bumps ``gate_clears_<gate_id>``
(quests use both).
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from config import GATE_LEVEL_GRACE, feature
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine
    from entities.gate import GateDefinition, GateRoom

logger = logging.getLogger(__name__)


def _status(engine: "GameEngine", gate: "GateDefinition") -> str:
    from core.lair_flow import get_state
    from systems import lair_system

    state = get_state(engine, gate.gate_id)
    if state is None:
        return ""
    if state.occupied:
        return f"ruled by {lair_system.occupant_title(gate.lair, state)}"
    return "its master is dead; something is moving in"


def gate_lines(engine: "GameEngine") -> list[tuple["GateDefinition", str, bool]]:
    """(gate, label, enterable) for every registered gate, lowest level first."""
    player = engine.state.player
    out = []
    for gate in engine.gate_registry.all():
        ok = player.level >= gate.min_level - GATE_LEVEL_GRACE
        done = " ✓" if player.has_flag(gate.clear_flag) else ""
        label = f"Lv {gate.min_level:>2} · {gate.name} [{gate.theme}]{done} — {_status(engine, gate)}"
        out.append((gate, label, ok))
    return out


def open_gate_registry(engine: "GameEngine") -> None:
    import questionary

    if not feature("boss_succession") or engine.gate_registry is None:
        renderer.print_system_message("The Gate Registry is closed.", style="dim_text")
        return
    while True:
        renderer.print_divider()
        renderer.print_system_message("SYSTEM GATE REGISTRY · ACTIVE RIFTS", style="system_msg")
        renderer.print_scene_text([
            "Since the Fracture, rifts have opened all over Aethoria. The System registers each one, "
            "rates it, and names whatever has made itself master of it. Masters die. Others take their place."
        ])
        choices = []
        for gate, label, ok in gate_lines(engine):
            if ok:
                choices.append(questionary.Choice(label, value=gate.gate_id))
            else:
                choices.append(questionary.Choice(label, value=gate.gate_id,
                                                  disabled=f"rated for level {gate.min_level}"))
        choices.append(questionary.Choice("← Leave the registry", value="__leave__"))
        pick = questionary.select("Which Gate?", choices=choices).ask()
        if pick in (None, "__leave__"):
            return
        gate = engine.gate_registry.get(pick)
        renderer.print_scene_text([gate.description])
        go = questionary.select("Enter it?", choices=["Enter the Gate.", "Not yet."]).ask()
        if go == "Enter the Gate.":
            run_gate(engine, pick)
            return


def _roll(loot, rng: random.Random) -> list[str]:
    return [item for item, chance in loot if rng.random() < chance]


def _give(engine: "GameEngine", items: list[str]) -> None:
    from core.event_bus import Event, bus
    player = engine.state.player
    for item_id in items:
        item = engine.item_registry.get(item_id)
        if item is None:
            continue
        player.add_item(item_id)
        bus.publish(Event("ITEM_FOUND", {"item_id": item_id, "item_name": item.name,
                                         "rarity": getattr(item.rarity, "value", str(item.rarity))}))
    bus.flush()


def _room(engine: "GameEngine", gate: "GateDefinition", idx: int, room: "GateRoom",
          rng: random.Random) -> str:
    """Play one room. Returns "ok", or the combat outcome that ends the run."""
    from systems.combat_system import spawn_enemy

    player = engine.state.player
    renderer.print_divider()
    renderer.print_system_message(f"{gate.name.upper()} · ROOM {idx + 1}/{len(gate.rooms)}", style="system_msg")
    renderer.print_scene_text([room.text])

    if room.kind == "fight":
        enemies = [e for e in (spawn_enemy(eid) for eid in room.enemies) if e is not None]
        outcome = engine._run_combat(f"gate:{gate.gate_id}:{idx}", enemies=enemies)
        return "ok" if outcome in ("victory", "none") else outcome

    if room.check is not None:
        c = room.check
        val = getattr(player.stats, c.stat.upper(), 0)
        if val >= c.min:
            renderer.print_scene_text([f"[{c.stat} {val} ≥ {c.min}] {c.success}".strip()])
            _give(engine, _roll(room.loot, rng))
        else:
            dmg = max(1, round(player.max_hp * c.damage_pct)) if c.damage_pct else 0
            player.current_hp = max(1, player.current_hp - dmg)
            hurt = f" You take {dmg} damage." if dmg else ""
            renderer.print_scene_text([f"[{c.stat} {val} < {c.min}] {c.fail}{hurt}".strip()])
            if room.kind == "treasure":
                _give(engine, [i for i in _roll(room.loot, rng)][:1])
    else:
        _give(engine, _roll(room.loot, rng))
    renderer.prompt_any_key()
    return "ok"


def run_gate(engine: "GameEngine", gate_id: str, rng: random.Random | None = None) -> str:
    """Run a gate start to finish. Returns "cleared", "withdrew", "fled",
    "defeat" or "none"."""
    import questionary
    from core.lair_flow import enter_lair

    gate = engine.gate_registry.get(gate_id) if engine.gate_registry else None
    if gate is None:
        logger.warning("enter_gate:%s: unknown gate", gate_id)
        return "none"
    player = engine.state.player
    rng = rng or random.Random(f"{gate_id}:{player.turn_count}:{player.player_id}")

    renderer.print_divider()
    renderer.print_system_message(f"ENTERING GATE · {gate.name.upper()} · RATED LEVEL {gate.min_level}",
                                  style="system_warning")
    renderer.print_scene_text([gate.entry_text])
    for idx, room in enumerate(gate.rooms):
        outcome = _room(engine, gate, idx, room, rng)
        if outcome != "ok":
            return outcome
        nxt = "Press on to the heart of the Gate." if idx == len(gate.rooms) - 1 else "Press on."
        go = questionary.select(f"HP {player.current_hp}/{player.max_hp}.",
                                choices=[nxt, "Withdraw through the rift."]).ask()
        if go != nxt:
            renderer.print_scene_text(["You back out through the rift. It will still be here."])
            return "withdrew"

    result = enter_lair(engine, gate.lair.lair_id)
    if result not in ("victory", "vacant"):
        return result
    player.set_flag(gate.clear_flag)
    key = f"gate_clears_{gate.gate_id}"
    player.set_flag(key, int(player.flags.get(key, 0) or 0) + 1)
    renderer.print_system_message(f"GATE CLEARED · {gate.name.upper()}", style="system_msg")
    return "cleared"


def bot_gate_view(engine: "GameEngine", lairs: dict | None = None) -> list[dict]:
    """Gates bots can run: id, name, min level, region, room enemy lists, and
    the lair entry from lair_flow.bot_lair_view (None when it can't be fought)."""
    from core.lair_flow import bot_lair_view
    from entities.gate import GATE_ZONE_PREFIX
    from systems.combat_system import spawn_enemy

    if not feature("boss_succession") or engine.gate_registry is None:
        return []
    lairs = bot_lair_view(engine) if lairs is None else lairs
    out = []
    for gate in engine.gate_registry.all():
        rooms = [[e for e in (spawn_enemy(x) for x in r.enemies) if e is not None]
                 for r in gate.rooms if r.kind == "fight"]
        lair = (lairs.get(GATE_ZONE_PREFIX + gate.gate_id) or [None])[0]
        out.append({"gate_id": gate.gate_id, "name": gate.name, "min_level": gate.min_level,
                    "region": gate.region, "rooms": rooms, "lair": lair,
                    "material": gate.material})
    return out
