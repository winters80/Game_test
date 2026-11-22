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

    # ── Content health: nothing unreachable, unoffered or unused ──
    from core.bootstrap import load_registries
    from core.content_health import check as health
    from core.game_engine import GameEngine
    eng = GameEngine()
    load_registries(eng)
    rep = health(eng)
    check(rep.problems() == 0, "content health: " + ("clean" if not rep.problems() else
                                                     "; ".join(rep.lines(limit=5))))

    # ── Quick sim: every class clears a spread of Gates at their rated level ──
    import scripts.balance_sim as bs
    gates = sorted(gr.all(), key=lambda g: g.min_level)
    sample = [g for g in gates if g.party_size <= 1][::5][:6]
    for cls in ("warrior", "mage", "rogue"):
        wins, hp = 0, 0.0
        runs = 0
        for g in sample:
            for i in range(6):
                ok, left = bs.run_gate(g, g.min_level, cls, (cr, sr), random.Random(i))
                runs += 1
                wins += ok
                hp += left
        rate = wins / runs
        check(rate >= 0.85, f"{cls}: wins {rate:.0%} of sampled Gates at their level")
        check(hp / runs <= 0.85, f"{cls}: ends with {hp / runs:.0%} HP on average (not trivial)")


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()
    main()
    print()
    if failures:
        print(f"  {len(failures)} balance check(s) failed.")
        sys.exit(1)
    print("  All balance checks passed.")
