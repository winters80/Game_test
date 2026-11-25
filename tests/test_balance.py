"""
Class identity and Gate difficulty: class milestones, the enemy scale knob,
and a quick balance sim so a change can't quietly make a class unable to
clear its Gates (or make every Gate trivial).

    python tests/test_balance.py

The full report is ``python scripts/balance_sim.py 24 --class mage``.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓  " if cond else "  ✗  ") + msg)
    if not cond:
        failures.append(msg)


def main() -> None:
    import config
    from config import DATA_DIR
    from entities.character_class import ClassRegistry
    from entities.gate import GateRegistry
    from entities.lair import LairRegistry
    from entities.player import Player
    from entities.skill import SkillRegistry
    from systems import class_system, level_system
    from systems.combat_system import ENEMY_TEMPLATES
    from systems.lair_system import register_enemy_templates

    cr = ClassRegistry()
    cr.load_from_file(DATA_DIR / "classes" / "base_classes.json")
    cr.load_from_file(DATA_DIR / "classes" / "combo_classes.json")
    sr = SkillRegistry()
    sr.load_from_dir(DATA_DIR / "skills")

    # ── Class milestones ──
    p = Player(name="M", base_class="mage")
    class_system.assign_base_class(p, "mage", cr, sr)
    int0, hp0 = p.stats.INT, p.max_hp
    every = config.CLASS_MILESTONE_EVERY
    while p.level < every:
        level_system.add_experience(p, level_system.xp_for_level(p.level), cr, sr)
    growth = cr.get("mage").stat_growth
    check(p.stats.INT == int0 + growth.INT, f"level {every}: the mage's class milestone adds INT +{growth.INT}")
    check(p.max_hp > hp0, "milestone VIT raises max HP")
    q = Player(name="N", base_class="mage")
    class_system.assign_base_class(q, "mage", cr, sr)
    i0 = q.stats.INT
    level_system.add_experience(q, level_system.xp_for_level(q.level), cr, sr)
    check(q.stats.INT == i0, "no milestone between milestone levels")
    r = Player(name="L")
    level_system.add_experience(r, level_system.xp_for_level(r.level) * 50)
    check(r.level > every, "callers without registries still level (no milestone, no crash)")

    rogue = cr.get("rogue")
    check(rogue.learnable_skills[0] == "evasion", "rogues learn Evasion first (their defining passive)")

    # ── Enemy scale knob ──
    lr = LairRegistry()
    lr.load_from_dir(DATA_DIR / "dungeons")
    gr = GateRegistry()
    gr.load_from_dir(DATA_DIR / "dungeons" / "gates", lr)
    register_enemy_templates(lr)
    eid, tmpl = next(iter(lr.enemy_templates.items()))
    check(ENEMY_TEMPLATES[eid]["max_hp"] == max(1, round(tmpl.max_hp * config.GATE_ENEMY_HP_SCALE)),
          f"Gate enemy HP is scaled by GATE_ENEMY_HP_SCALE ({config.GATE_ENEMY_HP_SCALE})")

    # ── Out-levelled content pays less XP; skills grow slower the higher they get ──
    fo = level_system.xp_falloff
    check(fo(5, 5) == 1.0 and fo(6, 5) == 1.0 and fo(5, 0) == 1.0, "full XP at or 1 level above the content, and for unrated fights")
    check(abs(fo(7, 5) - 0.8) < 1e-9 and fo(20, 2) == config.XP_FALLOFF_MIN, "20% less per further level, down to the floor")
    check(level_system.scaled_xp(100, 10, 2) >= 1 and level_system.scaled_xp(0, 10, 2) == 0, "scaled XP keeps at least 1")
    from systems.skill_system import uses_for_level
    check([uses_for_level(n) for n in (1, 2, 3, 10)] == [0, 5, 15, 225], "skill levels need 5, 15 … 225 casts")
    from systems.combat_system import Enemy, enemy_attack
    agile = Player(name="Dodger")
    agile.stats.AGI = 200
    agile.max_hp = agile.current_hp = 10_000
    random.seed(3)
    hits = sum(enemy_attack(Enemy(enemy_id="x", name="X", max_hp=10, current_hp=10, attack=20, defense=0), agile) > 0
               for _ in range(400))
    check(hits / 400 >= 1 - config.DODGE_CAP - 0.07, f"dodge is capped at {config.DODGE_CAP:.0%} ({1 - hits / 400:.0%} dodged)")

    from core.gate_flow import gate_xp_scale
    scales = {g.gate_id: gate_xp_scale(g) for g in gr.all()}
    check(all(0 < v <= 1 for v in scales.values()), "every Gate's XP scale is in (0, 1]")
    check(scales["ashgrove_manor"] < 0.5, f"early Gates pay far less than they did (Ashgrove x{scales['ashgrove_manor']:.2f})")

    # ── Content health: nothing unreachable, unoffered or unused ──
    from core.bootstrap import load_registries
    from core.content_health import check as health
    from core.game_engine import GameEngine
    eng = GameEngine()
    load_registries(eng)
    # A companion in a solo Gate makes its enemies tougher; group Gates already count the party.
    from systems.combat_system import spawn_enemy
    solo = next(g for g in eng.gate_registry.all() if g.party_size <= 1)
    group = next(g for g in eng.gate_registry.all() if g.party_size > 1)
    eid = next(r.enemies[0] for r in solo.rooms if r.kind == "fight")
    base = spawn_enemy(eid).max_hp
    eng._companions = [object()]
    e1 = [spawn_enemy(eid)]
    eng._scale_for_companions(f"gate:{solo.gate_id}:0", e1)
    e2 = [spawn_enemy(eid)]
    eng._scale_for_companions(f"gate:{group.gate_id}:0", e2)
    eng._companions = []
    e3 = [spawn_enemy(eid)]
    eng._scale_for_companions(f"gate:{solo.gate_id}:0", e3)
    check(e1[0].max_hp == round(base * (1 + config.GATE_COMPANION_HP)) and e2[0].max_hp == base == e3[0].max_hp,
          f"a companion makes a solo Gate's enemies {config.GATE_COMPANION_HP:.0%} tougher (not a group Gate's, not alone)")
    # ── Hard mode: tougher Gate / lair enemies, more gold, switched at the Registry ──
    from systems import difficulty
    from systems.combat_system import Enemy as _E
    foe = _E(enemy_id="x", name="X", max_hp=100, current_hp=100, attack=20)
    difficulty.harden([foe])
    check(foe.max_hp == round(100 * config.HARD_ENEMY_HP) and foe.attack == round(20 * config.HARD_ENEMY_ATK),
          f"Hard: enemies get +{config.HARD_ENEMY_HP - 1:.0%} HP and +{config.HARD_ENEMY_ATK - 1:.0%} attack")
    check(difficulty.applies_to("gate:x:0") and difficulty.applies_to("lair:crystal_heart")
          and not difficulty.applies_to("wolf_pack"), "Hard applies to Gates and boss lairs only")
    check(difficulty.hard_gold(100) == round(100 * (1 + config.HARD_GOLD_BONUS)), "Hard pays more gold")
    from ui import prompt as uip
    from core.gate_flow import _toggle_difficulty

    class _Yes:                               # answers every prompt with its first choice
        def select(self, message, choices, default=None):
            return choices[0].value
        def text(self, *a, **k): return ""
        def confirm(self, *a, **k): return True
        def any_key(self, *a, **k): return None

    from core.state_manager import GameState
    eng.state = GameState(Player(name="Brave"))
    uip.set_backend(_Yes())
    try:
        _toggle_difficulty(eng)
        on = difficulty.is_hard(eng.state.player)
        _toggle_difficulty(eng)
        off = not difficulty.is_hard(eng.state.player)
    finally:
        uip.set_backend(None)
    check(on and off, "the Gate Registry switches Hard mode on and back off")

    rep = health(eng)
    check(rep.problems() == 0, "content health: " + ("clean" if not rep.problems() else
                                                     "; ".join(rep.lines(limit=5))))

    # The lock check catches a circle: a door that needs a flag set only past the door.
    from core.content_health import ROOT as _HR, _locked_nodes
    _circle = {"prologue": {"nodes": {
        "root": {"options": [
            {"option_id": "door", "leads_to": "__stay__", "leads_to_node": "vault",
             "requires": {"flags": ["zz_key_found"]}},
            {"option_id": "hall", "leads_to": "__stay__", "leads_to_node": "hall"}]},
        "vault": {"options": [
            {"option_id": "take", "leads_to": "__stay__", "leads_to_node": "root", "triggers": ["flag:zz_key_found"]}]},
        "hall": {"options": []}}}}
    _all = {("prologue", "root"), ("prologue", "vault"), ("prologue", "hall")}
    _locked = _locked_nodes(_circle, _all, _HR / "data")
    check(len(_locked) == 1 and _locked[0].startswith("prologue.vault"),
          f"content health: a door locked by a flag set only behind it is reported ({_locked})")
    _circle["prologue"]["nodes"]["hall"]["options"].append(
        {"option_id": "find", "leads_to": "__stay__", "triggers": ["flag:zz_key_found"]})
    check(_locked_nodes(_circle, _all, _HR / "data") == [],
          "content health: ... and not once the flag can be found elsewhere")

    # ── Quick sim: every class clears a spread of Gates at their rated level ──
    import scripts.balance_sim as bs
    gates = sorted(gr.all(), key=lambda g: g.min_level)
    sample = [g for g in gates if g.party_size <= 1][::5][:6]
    for cls in ("warrior", "mage", "rogue"):
        for build in ("even", "focused"):
            wins, hp = 0, 0.0
            runs = 0
            for g in sample:
                for i in range(6):
                    ok, left = bs.run_gate(g, g.min_level, cls, (cr, sr), random.Random(i), build)
                    runs += 1
                    wins += ok
                    hp += left
            rate = wins / runs
            if build == "even":     # a careless build still clears its Gates
                check(rate >= 0.85, f"{cls} ({build}): wins {rate:.0%} of sampled Gates at their level")
            # a focused build (how play builds) still pays for it
            cap = 0.6 if build == "even" else 0.8
            check(hp / max(1, wins) <= cap, f"{cls} ({build}): ends with {hp / max(1, wins):.0%} HP on average (≤ {cap:.0%})")
        # Hard mode: a focused build still clears its Gates, and pays for it
        wins, hp, runs = 0, 0.0, 0
        for g in sample:
            for i in range(6):
                ok, left = bs.run_gate(g, g.min_level, cls, (cr, sr), random.Random(i), "focused", hard=True)
                runs += 1
                wins += ok
                hp += left
        check(wins / runs >= 0.85 and hp / max(1, wins) <= 0.65,
              f"{cls} (focused, Hard): wins {wins / runs:.0%}, ends with {hp / max(1, wins):.0%} HP (≤ 65%)")


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()
    main()
    print()
    if failures:
        print(f"  {len(failures)} balance check(s) failed.")
        sys.exit(1)
    print("  All balance checks passed.")
