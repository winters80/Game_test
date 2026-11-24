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

from config import GATE_BREATHER, GATE_GRACE_FROM_LEVEL, GATE_LEVEL_GRACE, feature
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


def entry_level(gate: "GateDefinition") -> int:
    """The lowest player level the Registry lets in: one under the rating,
    except for the first Gates, where one level is too big a step."""
    grace = GATE_LEVEL_GRACE if gate.min_level >= GATE_GRACE_FROM_LEVEL else 0
    return max(1, gate.min_level - grace)


def gate_lines(engine: "GameEngine") -> list[tuple["GateDefinition", str, bool]]:
    """(gate, label, enterable) for every registered gate, lowest level first."""
    player = engine.state.player
    out = []
    for gate in engine.gate_registry.all():
        ok = player.level >= entry_level(gate)
        done = " ✓" if player.has_flag(gate.clear_flag) else ""
        group = f" [party of {gate.party_size}]" if gate.party_size > 1 else ""
        label = f"Lv {gate.min_level:>2} · {gate.name} [{gate.theme}]{group}{done} — {_status(engine, gate)}"
        out.append((gate, label, ok))
    return out


def hall_news(engine: "GameEngine", limit: int = 4) -> list[str]:
    """Who's in the hall and the latest from the rifts (Gate Registry header)."""
    lines = []
    mgr = getattr(engine, "_bot_manager", None)
    if feature("bot_system") and mgr is not None:
        here = [b for b in mgr.all() if b.current_zone_id == engine.state.current_scene_id]
        if here:
            names = ", ".join(f"{b.name} (Lv {b.level})" for b in sorted(here, key=lambda b: -b.level)[:5])
            more = f" and {len(here) - 5} more" if len(here) > 5 else ""
            lines.append(f"In the hall: {names}{more}. Any of them might join a run.")
    db = engine.state.world_db if feature("world_db") else None
    if db is not None:
        try:
            events = [e for e in db.get_recent_events(30) if e.get("event_type") in ("lair", "bot_activity")]
        except Exception:
            events = []
        for e in events[:limit]:
            lines.append(f"· {e.get('event_text', '')}")
    return lines


def open_gate_registry(engine: "GameEngine") -> None:
    from ui import prompt as uip
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
        for line in hall_news(engine):
            renderer.console.print(f"  [dim_text]{line}[/dim_text]")
        choices = []
        for gate, label, ok in gate_lines(engine):
            if ok:
                choices.append(uip.Choice(label, value=gate.gate_id))
            else:
                choices.append(uip.Choice(label, value=gate.gate_id,
                                                  disabled=f"rated for level {gate.min_level}"))
        choices.append(uip.Choice("← Leave the registry", value="__leave__"))
        pick = uip.select("Which Gate?", choices=choices).ask()
        if pick in (None, "__leave__"):
            return
        gate = engine.gate_registry.get(pick)
        renderer.print_scene_text([gate.description])
        go = uip.select("Enter it?", choices=["Enter the Gate.", "Not yet."]).ask()
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

    from ui import media
    if room.kind == "trap":
        media.sfx("trap")
    elif room.kind == "treasure":
        media.sfx("chest_open")
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
    """Run a gate start to finish, with a bot companion if one is around and
    the player wants one. Returns "cleared", "withdrew", "fled", "defeat",
    "retreat" or "none"."""
    gate = engine.gate_registry.get(gate_id) if engine.gate_registry else None
    if gate is None:
        logger.warning("enter_gate:%s: unknown gate", gate_id)
        return "none"
    player = engine.state.player
    rng = rng or random.Random(f"{gate_id}:{player.turn_count}:{player.player_id}")
    gold_before = player.gold
    from ui import media
    media.on_gate(gate.gate_id, gate.name)
    party = _recruit(engine, gate)
    if party is None:
        return "no_party"
    engine._companions = party
    result = "none"
    try:
        result = _run(engine, gate, rng)
    finally:
        engine._companions = []
        if party:
            _pay_companions(engine, gate, party, result, gold_before)
    return result


COMPANION_SHARE = 0.25          # of the gold earned on the run, per companion (max 60% in all)
SOLO_OVERRIDE_LEVELS = 6        # this far above a group Gate's level you may go in alone
COMPANION_LEVEL_GAP = 3         # bots this far below the gate's level won't come


def companion_candidates(engine: "GameEngine", gate: "GateDefinition") -> list:
    mgr = getattr(engine, "_bot_manager", None)
    if not feature("bot_system") or mgr is None:
        return []
    here = engine.state.current_scene_id
    return sorted((b for b in mgr.all()
                   if b.current_zone_id == here and b.level >= gate.min_level - COMPANION_LEVEL_GAP
                   and b.hp > b.max_hp * 0.5 and b.archetype in ("fighter", "explorer", "gatherer")),
                  key=lambda b: (b.waiting_until <= engine.state.player.turn_count, -b.level))[:4]


def needed_companions(engine: "GameEngine", gate: "GateDefinition") -> int:
    """Companions a group Gate requires (0 for solo Gates, or when overlevelled)."""
    if gate.party_size <= 1 or engine.state.player.level >= gate.min_level + SOLO_OVERRIDE_LEVELS:
        return 0
    return gate.party_size - 1


def _recruit(engine: "GameEngine", gate: "GateDefinition") -> list | None:
    """Pick companions. Returns the party ([] = alone), or None if a group
    Gate can't be entered (not enough adventurers here, or the player backed out)."""
    from ui import prompt as uip
    from core.bot_flow import bot_label

    need = needed_companions(engine, gate)
    cands = companion_candidates(engine, gate)
    if need and len(cands) < need:
        renderer.print_system_message(
            f"GROUP GATE · needs a party of {gate.party_size}. Only {len(cands)} adventurer(s) here can come "
            f"(level {gate.min_level - COMPANION_LEVEL_GAP}+). Try again when more are about; [W] shows who's where.",
            style="system_warning")
        return None
    party: list = []
    max_party = 1 if gate.party_size <= 1 else gate.party_size - 1
    while cands and len(party) < max_party:
        labels = [f"Bring {bot_label(engine, b)} (takes {int(COMPANION_SHARE * 100)}% of the gold)" for b in cands]
        if len(party) < need:
            prompt = f"Group Gate (party of {gate.party_size}): choose {need - len(party)} more companion(s)."
            tail = ["Back out."]
        else:
            prompt = "Take a companion?" if not party else "Anyone else?"
            tail = ["Set off." if party else "Go alone."]
        pick = uip.select(prompt, choices=labels + tail).ask()
        if pick in labels:
            bot = cands.pop(labels.index(pick))
            party.append(bot)
            renderer.print_scene_text([f"{bot.name} checks their gear and nods. 'After you.'"])
            continue
        if pick == "Back out." or (pick is None and len(party) < need):
            return None
        break
    if len(party) < need:
        return None
    return party


def _pay_companions(engine: "GameEngine", gate: "GateDefinition", party: list, result: str,
                    gold_before: int) -> None:
    from core.bot_flow import _save_bot
    from systems.bot_brain import _gain_xp
    from systems.economy import recommended_quest_reward

    player = engine.state.player
    earned = max(0, player.gold - gold_before)
    rate = min(0.6, COMPANION_SHARE * len(party)) / len(party)
    for bot in party:
        share = int(earned * rate)
        player.gold -= share
        bot.gold += share
        if result == "cleared":
            _gain_xp(bot, recommended_quest_reward(gate.min_level, "standard")[1] // 2)
            bot.remember(f"Cleared {gate.name} with {player.name} (turn {player.turn_count})")
        else:
            bot.remember(f"Went into {gate.name} with {player.name}; it went badly (turn {player.turn_count})")
        bot.hp = max(1, bot.hp)
        bot.waiting_until = 0            # the promised run happened
        _save_bot(engine, bot)
    if result == "cleared":
        renderer.print_npc_response(party[0].name, "Good run. We'll take our share: that's fair. Find us if you go again.")


def _run(engine: "GameEngine", gate: "GateDefinition", rng: random.Random) -> str:
    from config import TIME_COSTS
    from core.lair_flow import enter_lair
    from core.time_flow import pass_time
    from ui import prompt as uip
    player = engine.state.player
    renderer.print_divider()
    renderer.print_system_message(f"ENTERING GATE · {gate.name.upper()} · RATED LEVEL {gate.min_level}",
                                  style="system_warning")
    renderer.print_scene_text([gate.entry_text])
    rounds = 0
    for idx, room in enumerate(gate.rooms):
        engine._last_combat_rounds = 0
        outcome = _room(engine, gate, idx, room, rng)
        rounds += getattr(engine, "_last_combat_rounds", 0) or 0
        pass_time(engine, TIME_COSTS["gate_room"])
        if outcome != "ok":
            return outcome
        breather(player)
        nxt = "Press on to the heart of the Gate." if idx == len(gate.rooms) - 1 else "Press on."
        go = uip.select(f"HP {player.current_hp}/{player.max_hp}.",
                                choices=[nxt, "Withdraw through the rift."]).ask()
        if go != nxt:
            renderer.print_scene_text(["You back out through the rift. It will still be here."])
            return "withdrew"

    engine._last_combat_rounds = 0
    result = enter_lair(engine, gate.lair.lair_id)
    pass_time(engine, TIME_COSTS["gate_master"])
    if result not in ("victory", "vacant"):
        return result
    rounds += getattr(engine, "_last_combat_rounds", 0) or 0
    key = f"gate_clears_{gate.gate_id}"
    first = not player.has_flag(gate.clear_flag)
    player.set_flag(gate.clear_flag)
    player.set_flag(key, int(player.flags.get(key, 0) or 0) + 1)
    if gate.gate_id.startswith("gen_"):
        player.set_flag("charted_new_rift")
    renderer.print_system_message(f"GATE CLEARED · {gate.name.upper()}", style="system_msg")
    _clear_bonuses(engine, gate, first, rounds, fought_boss=result == "victory")
    return "cleared"


def breather(player) -> tuple[int, int]:
    """Catch your breath between rooms: recover GATE_BREATHER of max HP and MP."""
    hp = min(player.max_hp - player.current_hp, round(player.max_hp * GATE_BREATHER))
    mp = min(player.max_mp - player.current_mp, round(player.max_mp * GATE_BREATHER))
    player.current_hp += max(0, hp)
    player.current_mp += max(0, mp)
    return hp, mp


def swift_par(gate: "GateDefinition") -> int:
    """Combat rounds a swift clear may take: 3 per fight room, 6 for the master."""
    return 3 * sum(1 for r in gate.rooms if r.kind == "fight") + 6


def _clear_bonuses(engine: "GameEngine", gate: "GateDefinition", first: bool, rounds: int,
                   fought_boss: bool) -> list[str]:
    """FIRST CLEAR pays a standard quest reward for the gate's level; a SWIFT
    CLEAR (every fight, master included, within ``swift_par`` rounds) pays
    half that again. Returns the bonus lines shown."""
    from config import COPPER_PER_GOLD, format_currency
    from systems import level_system
    from systems.economy import recommended_quest_reward

    player = engine.state.player
    gold, xp = recommended_quest_reward(gate.min_level, "standard")
    lines = []
    total_gold = total_xp = 0
    if first:
        total_gold += gold
        total_xp += xp
        lines.append(f"✦ FIRST CLEAR · +{format_currency(gold * COPPER_PER_GOLD)}, +{xp} XP")
    if fought_boss and 0 < rounds <= swift_par(gate):
        g2, x2 = gold // 2, xp // 2
        total_gold += g2
        total_xp += x2
        best = f"swift_{gate.gate_id}"
        if not player.has_flag(best) or rounds < int(player.flags.get(best) or 999):
            player.set_flag(best, rounds)
        lines.append(f"✦ SWIFT CLEAR ({rounds} rounds, par {swift_par(gate)}) · "
                     f"+{format_currency(g2 * COPPER_PER_GOLD)}, +{x2} XP")
    if total_gold or total_xp:
        player.gold += total_gold * COPPER_PER_GOLD
        level_system.add_experience(player, total_xp, class_registry=engine.class_registry,
                                    skill_registry=engine.skill_registry)
        for line in lines:
            renderer.print_system_message(line, style="system_msg")
        from core.event_bus import bus
        bus.flush()
    return lines


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
                    "material": gate.material, "party_size": gate.party_size})
    return out
