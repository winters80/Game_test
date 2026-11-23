"""
The Ledger in play (rules in systems/law_system.py).

- ``tick_law`` (every turn): heat cools, the System reports sedition, and a
  warden patrol or a band of Ledger hunters may come for a wanted player.
- ``confront`` : the encounter. Pay the fine, go quietly (time in the
  cells), talk your way out, run, or fight (an assault on the Ledger).
  Losing to wardens means the cells, not death.
- ``settle_at_counter`` / ``surrender`` / ``warrant_board`` : the Ledger House.
- ``arrest_bot`` : serve a warrant on a wanted bot adventurer.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from config import feature, format_currency
from systems import law_system as law
from ui import prompt as uip
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)

NO_LAW_SCENES = ("prologue", "character_creation", "ending_rule", "ending_destroy")


def _standing(engine) -> float:
    return law.ledger_standing(engine.state)


def _update_standing(engine, delta: float) -> None:
    if getattr(engine, "faction_registry", None) and feature("faction_system"):
        engine._update_faction_standing(law.LEDGER, delta)
    elif engine.state.world_db is not None:
        engine.state.world_db.update_faction_standing(law.LEDGER, delta)


def tick_law(engine: "GameEngine", rng: random.Random | None = None) -> str:
    """Per-turn Ledger bookkeeping. Returns the encounter run ('' if none)."""
    state = engine.state
    if state is None or state.current_scene_id in NO_LAW_SCENES:
        return ""
    p = state.player
    law.cool_down(p, 1)
    if p.has_flag("criminal_record") and not p.has_flag("_ledger_knows_record"):
        p.set_flag("_ledger_knows_record")             # the Thief background
        _update_standing(engine, -10)
    for res in law.check_sedition(state):
        renderer.print_system_message(
            f"⚖ THE SYSTEM HAS REPORTED YOU TO THE LEDGER · {law.CRIMES['sedition'].label.upper()} "
            f"· heat {res.heat:.0f}", style="system_warning")
    kind = law.enforcement_due(state, rng)
    if kind:
        confront(engine, kind, rng)
    return kind


def _wardens(level: int, kind: str) -> list:
    from systems.combat_system import Enemy
    from systems.gate_generation import mob_stats
    n = 3 if kind == "hunters" else 2
    lvl = max(1, level + (1 if kind == "hunters" else 0))
    out = []
    for i in range(n):
        s = mob_stats(lvl)
        name = "Ledger Hunter" if kind == "hunters" and i == 0 else "Ledger Warden"
        out.append(Enemy(enemy_id=f"ledger_{kind}_{i}", name=name, max_hp=s["max_hp"], current_hp=s["max_hp"],
                         attack=s["attack"], defense=s["defense"], xp_reward=s["xp_reward"] // 2,
                         gold_reward=0, rank=s["rank"],
                         status_on_hit=("stun", 0.12) if i == 0 else None))
    return out


def _deputy_here(engine) -> str:
    mgr = getattr(engine, "_bot_manager", None)
    if mgr is None:
        return ""
    for b in mgr.all():
        if b.current_zone_id == engine.state.current_scene_id and b.allegiance == "ledger" \
                and b.jailed_until <= engine.state.player.turn_count:
            return b.name
    return ""


def confront(engine: "GameEngine", kind: str = "patrol", rng: random.Random | None = None) -> str:
    """A warden patrol (in town) or Ledger hunters (anywhere) stop the
    player. Returns 'paid' | 'jailed' | 'talked' | 'escaped' | 'fought' | 'beaten'."""
    from utils import session_log
    rng = rng or random
    p = engine.state.player
    fine = law.fine_for(p, _standing(engine))
    deputy = _deputy_here(engine)
    session_log.event("ledger_" + kind, heat=round(p.heat), bounty=p.bounty)
    renderer.print_divider()
    if kind == "hunters":
        renderer.print_scene_text([
            "Three figures in grey coats have been following you for a while. When you stop, they spread out.",
            "'The Ledger has your name,' the lead hunter says, and opens a writ. 'It would like the rest of you.'"])
    else:
        who = f"{deputy}, deputised for the Ledger, steps" if deputy else "A warden in a grey coat steps"
        renderer.print_scene_text([
            f"{who} into your path, a ledger open in one hand.",
            f"'Your record doesn't balance. {format_currency(fine)} settles it, or you come with us.'"])
    renderer.console.print(f"  [dim_text]Heat {p.heat:.0f} · bounty {format_currency(p.bounty)} · "
                           f"{law.heat_label(p.heat)}[/dim_text]")
    talk_chance = max(0.05, min(0.8, 0.25 + (p.stats.INT + p.stats.LCK) / 80 - p.heat / 200))
    run_chance = max(0.05, min(0.85, 0.3 + p.stats.AGI / 40 - (0.15 if kind == "hunters" else 0)))
    choices = [
        uip.Choice(f"Pay the fine ({format_currency(fine)})", "pay",
                   disabled=None if p.gold >= fine else "Not enough gold"),
        uip.Choice(f"Go quietly ({law.jail_turns(p) // 6} hours in the Ledger's cells)", "jail"),
        uip.Choice(f"Talk your way out (INT + LCK, about {talk_chance:.0%})", "talk",
                   disabled="Hunters don't negotiate" if kind == "hunters" else None),
        uip.Choice(f"Run (AGI, about {run_chance:.0%})", "run"),
        uip.Choice("Fight (an assault on the Ledger)", "fight"),
    ]
    pick = uip.select("The Ledger:", choices=choices).ask() or "jail"

    if pick == "pay":
        p.gold -= fine
        law.settle(p)
        _update_standing(engine, 2)
        renderer.print_success(f"You pay {format_currency(fine)}. The entry is settled.")
        result = "paid"
    elif pick == "talk":
        if rng.random() < talk_chance:
            p.heat = max(0.0, p.heat - 10)
            renderer.print_scene_text(["The warden listens, sighs, and writes something small in the margin. "
                                       "'A warning. This time.'"])
            result = "talked"
        else:
            p.bounty += fine // 4
            renderer.print_scene_text(["'Lying to the Ledger,' the warden says, writing, 'is also an entry.'"])
            return confront_after_fail(engine, kind, rng)
    elif pick == "run":
        if rng.random() < run_chance:
            p.heat = min(100.0, p.heat + 5)
            renderer.print_scene_text(["You're gone before the whistle. They'll remember your face, though."])
            result = "escaped"
        else:
            renderer.print_scene_text(["A hand closes on your collar. So much for running."])
            result = _fight(engine, kind)
    elif pick == "fight":
        result = _fight(engine, kind)
    else:
        _jail(engine)
        result = "jailed"
    renderer.prompt_any_key()
    return result


def confront_after_fail(engine, kind, rng) -> str:
    """A failed talk: pay up, go quietly or fight; no second excuse."""
    p = engine.state.player
    fine = law.fine_for(p, _standing(engine))
    pick = uip.select("The Ledger:", choices=[
        uip.Choice(f"Pay the fine ({format_currency(fine)})", "pay",
                   disabled=None if p.gold >= fine else "Not enough gold"),
        uip.Choice("Go quietly", "jail"),
        uip.Choice("Fight", "fight")]).ask() or "jail"
    if pick == "pay":
        p.gold -= fine
        law.settle(p)
        renderer.print_success(f"You pay {format_currency(fine)}.")
        result = "paid"
    elif pick == "fight":
        result = _fight(engine, kind)
    else:
        _jail(engine)
        result = "jailed"
    renderer.prompt_any_key()
    return result


def _fight(engine, kind: str) -> str:
    """Fight the wardens. Win: an assault on the Ledger (more heat). Lose:
    the cells, not death."""
    p = engine.state.player
    outcome = engine._run_combat(f"ledger_{kind}", enemies=_wardens(p.level, kind), nonlethal=True)
    if outcome == "victory":
        res = law.commit_crime(engine.state, "assault")
        if res and not res.witnessed:                # you just beat up the wardens
            law.commit_crime(engine.state, "assault", rng=random.Random(0), zone="verath_city")
        renderer.print_system_message("The wardens are down. The Ledger will remember this.",
                                      style="system_warning")
        return "fought"
    renderer.print_scene_text(["You come to in a grey cell, your purse lighter."])
    fine = min(p.gold, law.fine_for(p))
    p.gold -= fine
    _jail(engine, extra_hours=4)
    return "beaten"


def _jail(engine, extra_hours: int = 0) -> None:
    """Time in the Ledger's cells: hours pass, stolen goods are confiscated,
    the record is settled."""
    from core.time_flow import pass_time
    p = engine.state.player
    turns = law.jail_turns(p) + extra_hours * 6
    taken = sum(s.quantity for s in p.inventory if s.item_id == "stolen_jewellery")
    if taken:
        p.remove_item("stolen_jewellery", taken)
    law.settle(p)
    _update_standing(engine, 3)
    renderer.print_scene_text([
        f"The cell is clean and quiet and nobody answers questions. {turns // 6} hours later a clerk unlocks the door.",
        "'Settled,' the clerk says, and closes the book." + (" Your stolen goods stay with the Ledger." if taken else "")])
    pass_time(engine, turns)


# ── The Ledger House ─────────────────────────────────────────────────────────

def settle_at_counter(engine: "GameEngine") -> None:
    p = engine.state.player
    if p.heat < 1 and p.bounty <= 0:
        renderer.print_system_message("The clerk checks the book. 'Your record balances. Good day.'",
                                      style="dim_text")
        renderer.prompt_any_key()
        return
    fine = law.fine_for(p, _standing(engine))
    if uip.confirm(f"Pay {format_currency(fine)} to settle your record? "
                   f"(heat {p.heat:.0f}, bounty {format_currency(p.bounty)})", default=True).ask():
        if p.gold < fine:
            renderer.print_error("You don't have enough. You could turn yourself in instead.")
        else:
            p.gold -= fine
            law.settle(p)
            _update_standing(engine, 2)
            renderer.print_success("Settled. The clerk blots the line dry.")
    renderer.prompt_any_key()


def surrender(engine: "GameEngine") -> None:
    p = engine.state.player
    if p.heat < 1 and p.bounty <= 0:
        renderer.print_system_message("'There's nothing to serve time for,' the clerk says. 'Go home.'",
                                      style="dim_text")
        renderer.prompt_any_key()
        return
    _jail(engine)
    renderer.prompt_any_key()


def wanted_bots(engine) -> list:
    mgr = getattr(engine, "_bot_manager", None)
    if mgr is None:
        return []
    turn = engine.state.player.turn_count
    return sorted((b for b in mgr.all() if b.heat >= 25 and b.jailed_until <= turn),
                  key=lambda b: -b.heat)


def warrant_board(engine: "GameEngine") -> None:
    from systems.bot_brain import place
    p = engine.state.player
    renderer.console.print("\n  [system_msg][ WARRANTS ][/system_msg]\n")
    rows = wanted_bots(engine)
    if not rows:
        renderer.console.print("  [dim_text]No open warrants. Verath is quiet, for now.[/dim_text]")
    for b in rows[:8]:
        renderer.console.print(f"  {b.name} (Lv {b.level})  ·  heat {b.heat:.0f}  ·  last seen at {place(b.current_zone_id)}")
    standing = _standing(engine)
    you = f"heat {p.heat:.0f}, bounty {format_currency(p.bounty)}" if (p.heat >= 1 or p.bounty) else "clean"
    renderer.console.print(f"\n  [dim_text]Your record: {you} · Ledger standing {standing:+.0f} "
                           f"({law.heat_label(p.heat)})[/dim_text]")
    if rows and standing >= 0 and p.heat < law.PATROL_HEAT:
        renderer.console.print("  [dim_text]Find one of them and talk to them to serve the warrant.[/dim_text]")
    elif rows:
        renderer.console.print("  [dim_text]The Ledger only deputises people whose own record is clean "
                               "and in good standing.[/dim_text]")
    renderer.console.print()
    renderer.prompt_any_key()


def can_arrest(engine, bot) -> bool:
    p = engine.state.player
    return (bot.heat >= 25 and bot.jailed_until <= p.turn_count and _standing(engine) >= 0
            and p.heat < law.PATROL_HEAT)


def arrest_bot(engine: "GameEngine", bot) -> str:
    """Serve the Ledger's warrant on a wanted bot: they come quietly if
    you outclass them, otherwise it's a (non-lethal) fight."""
    from systems.bot_brain import bot_attack, bot_defense
    from systems.combat_system import Enemy
    p = engine.state.player
    if bot.level <= p.level - 3:
        outcome = "victory"
        renderer.print_scene_text([f"{bot.name} looks at you, at the writ, and holds out their wrists."])
    else:
        foe = Enemy(enemy_id=f"bot_{bot.bot_id}", name=bot.name, max_hp=bot.max_hp, current_hp=bot.hp,
                    attack=bot_attack(bot), defense=bot_defense(bot), xp_reward=10 * bot.level, gold_reward=0)
        renderer.print_scene_text([f"'You and whose Ledger?' {bot.name} says, and goes for you."])
        outcome = engine._run_combat(f"arrest_{bot.bot_id}", enemies=[foe], nonlethal=True)
    if outcome != "victory":
        bot.remember(f"Fought off {p.name}, who came with a Ledger warrant (turn {p.turn_count})")
        return "failed"
    reward = min(bot.gold, int(bot.heat * 40)) // 2 + 500
    bot.gold = max(0, bot.gold - reward)
    p.gold += reward
    bot.jailed_until = p.turn_count + max(12, int(bot.heat // 10) * 6)
    bot.heat = 0.0
    bot.hp = max(1, bot.hp)
    bot.activity = "in the Ledger's cells"
    bot.remember(f"Dragged to the Ledger cells by {p.name} (turn {p.turn_count})")
    p.set_flag("ledger_warrant_served")
    _update_standing(engine, 5)
    renderer.print_success(f"{bot.name} is taken to the Ledger's cells. The bounty pays you {format_currency(reward)}.")
    from core.bot_flow import _save_bot
    _save_bot(engine, bot)
    return "arrested"
