"""
Engine side of boss succession (logic: systems/lair_system.py, content:
data/dungeons/*.json).

- ``setup_lairs``       — per save: load who holds each lair, or seed the first occupants
- ``enter_lair``        — the ``boss_lair:LAIR_ID`` trigger: meet, study, fight the
                          current occupant, choose what to do with the body
- ``tick_lairs``        — once per turn: successors move in, rumours spread
- ``bot_killed_boss``   — a bot adventurer cleared a lair; the chain moves on without you
- ``bot_lair_view``     — what bots can see of the lairs (for systems/bot_brain)

State is kept in ``engine._lair_states`` and written through to the world DB
(``dungeon_lairs``, ``lair_history``) when it's open.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import feature
from systems import lair_system
from systems.lair_system import KillInfo, LairState
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine
    from entities.lair import LairDefinition

logger = logging.getLogger(__name__)

SAFE_ZONES = {"village_start", "camp_rest", "verath_city"}


def _db(engine: "GameEngine"):
    return engine.state.world_db if feature("world_db") and engine.state is not None else None


def _turn(engine: "GameEngine") -> int:
    return engine.state.player.turn_count


def setup_lairs(engine: "GameEngine") -> None:
    engine._lair_states = {}
    if not feature("boss_succession") or engine.lair_registry is None:
        return
    db = _db(engine)
    stored = {}
    if db is not None:
        try:
            stored = db.load_lair_states()
        except Exception:
            logger.warning("Could not load lair states", exc_info=True)
    for lair in engine.lair_registry.all():
        state = None
        if lair.lair_id in stored:
            try:
                state = LairState.model_validate(stored[lair.lair_id])
            except Exception:
                logger.warning("Bad stored state for lair %s; reseeding", lair.lair_id, exc_info=True)
        if state is None or lair.occupant(state.occupant_id) is None:
            state = lair_system.initial_state(lair)
            _save(engine, state)
        engine._lair_states[lair.lair_id] = state


def get_state(engine: "GameEngine", lair_id: str) -> LairState | None:
    return getattr(engine, "_lair_states", {}).get(lair_id)


def _save(engine: "GameEngine", state: LairState) -> None:
    db = _db(engine)
    if db is not None:
        try:
            db.upsert_lair_state(state.lair_id, state.model_dump(mode="json"), _turn(engine))
        except Exception:
            logger.warning("Could not save lair %s", state.lair_id, exc_info=True)
    if engine.state is not None:
        engine.state.mark_dirty()


def _log(engine: "GameEngine", text: str, zone_id: str, title: str = "Dungeon") -> None:
    db = _db(engine)
    if db is not None:
        try:
            db.store_world_event("lair", text, zone_id=zone_id, title=title,
                                 generated_turn=_turn(engine))
        except Exception:
            logger.info("Could not log lair event", exc_info=True)


def _finish_kill(engine: "GameEngine", lair: "LairDefinition", state: LairState, kill: KillInfo) -> dict:
    row = lair_system.record_kill(lair, state, kill, _turn(engine))
    _save(engine, state)
    db = _db(engine)
    if db is not None:
        try:
            db.add_lair_history(row)
        except Exception:
            logger.warning("Could not store lair history", exc_info=True)
    return row


# ── The player at the lair ────────────────────────────────────────────────────

def _previous_line(engine: "GameEngine", lair_id: str) -> str:
    db = _db(engine)
    if db is None:
        return ""
    history = db.get_lair_history(lair_id)
    if not history:
        return ""
    last = history[-1]
    who = "you" if last["killer_kind"] == "player" else f"{last['killer_name']} (Lv {last['killer_level']})"
    return f"Previous occupant: {last['occupant_name']}, slain by {who}."


def _grant_clear_flags(engine: "GameEngine", lair: "LairDefinition") -> None:
    for flag in lair.clear_flags:
        engine.state.player.set_flag(flag)


def enter_lair(engine: "GameEngine", lair_id: str) -> str:
    """Run the boss room. Returns "victory", "fled", "defeat", "vacant",
    "retreat" or "none"."""
    import questionary

    lair = engine.lair_registry.get(lair_id) if engine.lair_registry else None
    state = get_state(engine, lair_id)
    if lair is None or state is None:
        logger.warning("boss_lair:%s: unknown lair", lair_id)
        return "none"
    player = engine.state.player

    renderer.print_divider()
    if not state.occupied:
        renderer.print_system_message(
            f"ENCOUNTER ZONE VACANT · {lair.name.upper()} · NEW OCCUPANT EXPECTED", style="system_msg")
        lines = [lair_system.omen_text(lair, state)]
        prev = _previous_line(engine, lair_id)
        if prev:
            lines.append(prev)
        renderer.print_scene_text(lines)
        if any(not player.has_flag(f) for f in lair.clear_flags):
            _grant_clear_flags(engine, lair)
            renderer.print_scene_text(["With nothing to stop you, you cross the chamber and mark the way onward."])
        renderer.prompt_any_key()
        return "vacant"

    occ = lair.occupants[state.occupant_id]
    name = state.lineage_name
    title = lair_system.occupant_title(lair, state)
    gen = f" · GENERATION {state.generation + 1}" if state.generation else ""
    renderer.print_system_message(
        f"DESIGNATED ENCOUNTER ZONE · {title.upper()}{gen}", style="system_warning")
    lines = [lair_system.fmt(occ.intro, name)]
    if state.generation:
        prev = _previous_line(engine, lair_id)
        if prev:
            lines.append(prev)
    if lair_system.scaling(state) > 1.0:
        lines.append("The System notes: this occupant has held the lair before. It has grown stronger.")
    renderer.print_scene_text(lines)

    studied_flag = f"studied_{lair_id}_g{state.generation}"
    while True:
        fight = f"Fight {title}."
        choices = [fight]
        study_label = None
        if occ.study and not player.has_flag(studied_flag):
            stat_val = getattr(player.stats, occ.study.stat.upper(), 0)
            study_label = f"Study it before engaging. [{occ.study.stat} {occ.study.min}]"
            if stat_val >= occ.study.min:
                choices.append(study_label)
            else:
                choices.append(questionary.Choice(f"🔒 {study_label}", disabled="not sharp enough yet"))
        choices.append("Retreat.")
        answer = questionary.select("What do you do?", choices=choices).ask()
        if answer in (None, "Retreat."):
            return "retreat"
        if answer == study_label:
            player.set_flag(studied_flag)
            renderer.print_scene_text([lair_system.fmt(occ.study.text, name),
                                       "You'll have the first strike."])
            continue
        break

    enemies = lair_system.spawn_occupant(lair, state)
    outcome = engine._run_combat(f"lair:{lair_id}", surprise=player.has_flag(studied_flag), enemies=enemies)
    if outcome != "victory":
        return outcome

    renderer.print_divider()
    if occ.victory:
        renderer.print_scene_text([lair_system.fmt(occ.victory, name)])
    aftermath = occ.default_aftermath
    if occ.aftermath:
        labels = [lair_system.fmt(a.label, name) for a in occ.aftermath]
        pick = questionary.select("The fight is over. What now?", choices=labels).ask()
        chosen = occ.aftermath[labels.index(pick)] if pick in labels else occ.aftermath[0]
        aftermath = chosen.id
        if chosen.text:
            renderer.print_scene_text([lair_system.fmt(chosen.text, name)])
        if chosen.triggers:
            from core.event_bus import bus
            from scenes.option_logic import process_triggers
            process_triggers(chosen.triggers, engine.state)
            bus.flush()

    kill = KillInfo(
        killer_kind="player", killer_name=player.name, killer_level=player.level,
        killed_by=getattr(engine, "_last_killing_blow", "physical"),
        aftermath=aftermath, alignment=player.alignment,
    )
    _finish_kill(engine, lair, state, kill)
    _grant_clear_flags(engine, lair)
    player.set_flag(f"slew_{occ.occupant_id}")
    _log(engine, f"{player.name} slew {title} in {lair.name}.", lair.zone_id)
    renderer.print_system_message(
        "ENCOUNTER ZONE CLEARED. The System notes: something will take its place.", style="system_msg")
    renderer.prompt_any_key()
    return "victory"


# ── The world moves on ───────────────────────────────────────────────────────

def tick_lairs(engine: "GameEngine") -> list[str]:
    """Move successors into vacant lairs whose time has come. Returns the
    rumours spread (shown when the player is in a town or on that floor)."""
    if not feature("boss_succession") or engine.lair_registry is None or engine.state is None:
        return []
    turn = _turn(engine)
    rumours = []
    for lair in engine.lair_registry.all():
        state = get_state(engine, lair.lair_id)
        if state is None or state.occupied:
            continue
        prev_id, prev_name = state.occupant_id, state.lineage_name
        if not lair_system.tick(lair, state, turn):
            continue
        _save(engine, state)
        text = lair_system.rumour_text(lair, prev_id, prev_name, state.occupant_id, state.lineage_name) or (
            f"Something new has moved into {lair.name}.")
        _log(engine, text, lair.zone_id, title="Rumour")
        rumours.append(text)
        here = engine.state.current_scene_id
        if here in SAFE_ZONES or here == lair.zone_id:
            renderer.console.print(f"  [dim_text]✦ RUMOUR — {text}[/dim_text]")
    return rumours


def bot_killed_boss(engine: "GameEngine", lair_id: str, bot, killed_by: str) -> dict | None:
    lair = engine.lair_registry.get(lair_id) if engine.lair_registry else None
    state = get_state(engine, lair_id)
    if lair is None or state is None or not state.occupied:
        return None
    title = lair_system.occupant_title(lair, state)
    kill = KillInfo(killer_kind="bot", killer_name=bot.name, killer_level=bot.level,
                    killed_by="magic" if killed_by == "magic" else "physical")
    row = _finish_kill(engine, lair, state, kill)
    _log(engine, f"{bot.name} (Lv {bot.level}) slew {title} in {lair.name}. "
                 f"Something stirs there already.", lair.zone_id, title="Adventurers")
    return row


def bot_lair_view(engine: "GameEngine") -> dict[str, list[dict]]:
    """zone → occupied lairs bots could challenge: id, title, min level, enemies."""
    from config import LAIR_BOT_GRACE_TURNS

    view: dict[str, list[dict]] = {}
    if not feature("boss_succession") or engine.lair_registry is None:
        return view
    for lair in engine.lair_registry.all():
        state = get_state(engine, lair.lair_id)
        if state is None or not state.occupied:
            continue
        if state.generation == 0 and _turn(engine) < LAIR_BOT_GRACE_TURNS:
            continue      # the player gets the first shot at the original boss
        view.setdefault(lair.zone_id, []).append({
            "lair_id": lair.lair_id, "lair_name": lair.name, "zone_id": lair.zone_id,
            "title": lair_system.occupant_title(lair, state),
            "min_level": lair.min_level,
            "enemies": lair_system.spawn_occupant(lair, state),
        })
    return view


def lair_status_lines(engine: "GameEngine") -> list[str]:
    """One line per lair: who holds it now, or who's on the way."""
    lines = []
    if engine.lair_registry is None:
        return lines
    for lair in engine.lair_registry.all():
        state = get_state(engine, lair.lair_id)
        if state is None:
            continue
        where = lair.name[:1].upper() + lair.name[1:]
        if state.occupied:
            lines.append(f"{where}: {lair_system.occupant_title(lair, state)} "
                         f"(generation {state.generation + 1}, {state.kills} slain so far)")
        else:
            lines.append(f"{where}: empty; {lair_system.occupant_title(lair, state, next_one=True)} "
                         f"arrives turn {state.arrives_turn}")
    return lines
