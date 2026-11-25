"""
Balance pass: can a player at a Gate's rated level clear it?

For every Gate, builds a character at the Gate's level the way a player
would get there (class base stats + 3 points per level, spent on the class's
main stats; group Gates get level-matched bot companions), then runs the rooms and the first master with the real combat rules
(systems/combat_system.resolve_combat_auto: statuses, boss abilities, enrage,
and bot companions for group Gates).
Between rooms it drinks a potion if below half HP (two potions per run).
Each round it casts its best affordable damage skill, else a basic attack.

    python scripts/balance_sim.py            # 20 runs per Gate
    python scripts/balance_sim.py 50 --class mage
    python scripts/balance_sim.py --build focused   # how play actually builds

Builds: ``even`` spreads stat points over the class's three stats and keeps
skills at level 1 (a careless player). ``focused`` puts three points in four
into the main stat and the fourth into VIT, and gives each skill the level
CASTS_PER_LEVEL casts per character level would reach (what autoplay's
characters look like). A Gate should be winnable for ``even`` and still cost
``focused`` a real share of HP.

Prints a win-rate table, flagging Gates that are too hard (<40%) or trivial
(100% with HP to spare). Use it after changing Gate stats or player curves.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUILDS = {
    "warrior": ("STR", "VIT", "END"),
    "mage": ("INT", "VIT", "WIS"),
    "rogue": ("AGI", "STR", "VIT"),
}


CASTS_PER_LEVEL = 8      # focused build: skill casts per character level


def make_player(level: int, cls: str, class_registry, skill_registry=None, build: str = "even"):
    from entities.player import Player
    from systems import class_system, level_system

    p = Player(name="Sim", base_class=cls)
    if class_registry.get(cls):
        class_system.assign_base_class(p, cls, class_registry, skill_registry)
    style, stats = build, BUILDS.get(cls, BUILDS["warrior"])
    i = 0                       # counts every point spent, across levels
    while p.level < level:
        # With the registries, like real play: class skills are learned on
        # level-up and class milestones apply.
        level_system.add_experience(p, level_system.xp_for_level(p.level), class_registry, skill_registry)
        while p.stat_points > 0:
            if style == "focused":
                level_system.spend_stat_point(p, stats[0] if i % 4 != 3 else "VIT")
            else:
                level_system.spend_stat_point(p, stats[i % len(stats)])
            i += 1
    if style == "focused" and skill_registry is not None:
        from systems.skill_system import uses_for_level
        casts = CASTS_PER_LEVEL * level
        for sid in p.skills:
            sk = skill_registry.get(sid)
            top = getattr(sk, "max_level", 10) if sk else 10
            lv = 1
            while lv < top and uses_for_level(lv + 1) <= casts:
                lv += 1
            p.skill_levels[sid] = lv
    p.current_hp, p.current_mp = p.max_hp, p.max_mp
    return p


def run_gate(gate, level: int, cls: str, regs, rng: random.Random, build: str = "even") -> tuple[bool, float]:
    from systems.combat_system import resolve_combat_auto, spawn_enemy
    from systems.lair_system import initial_state, spawn_occupant

    class_registry, skill_registry = regs
    p = make_player(level, cls, class_registry, skill_registry, build)
    potions = 2
    from systems.bot_system import BotAgent, bot_max_hp
    companions = []
    for i in range(gate.party_size - 1):                # bot companions at the Gate's level
        b = BotAgent(bot_id=f"c{i}", name=f"C{i}", archetype="fighter")
        b.level = level
        b.max_hp = b.hp = bot_max_hp(b)
        companions.append(b)
    fights = [[spawn_enemy(e) for e in r.enemies] for r in gate.rooms if r.kind == "fight"]
    fights.append(spawn_occupant(gate.lair, initial_state(gate.lair)))
    from core.gate_flow import breather
    for i, enemies in enumerate(fights):
        if i:
            breather(p)                                # same as between real Gate rooms
        if p.current_hp < p.max_hp * 0.5 and potions:
            potions -= 1
            p.current_hp = min(p.max_hp, p.current_hp + (80 if level >= 6 else 30))
        res = resolve_combat_auto(p, [e for e in enemies if e], skill_registry, companions, use_skills=True)
        if not res.victory:
            return False, 0.0
    return True, p.current_hp / p.max_hp


def main() -> None:
    from unittest import mock

    from core.game_engine import GameEngine
    from ui import renderer

    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="?", type=int, default=20)
    ap.add_argument("--class", dest="cls", default="warrior", choices=sorted(BUILDS))
    ap.add_argument("--offset", type=int, default=0, help="player level relative to the Gate's")
    ap.add_argument("--build", default="even", choices=("even", "focused"))
    args = ap.parse_args()

    with mock.patch.object(renderer.console, "print", lambda *a, **k: None):
        eng = GameEngine()
        eng.bootstrap()
    regs = (eng.class_registry, eng.skill_registry)
    from core.gate_flow import entry_level
    rng = random.Random(7)
    flagged = 0
    print(f"{'Gate':<42} {'Lv':>3} {'Party':>5} {'Win%':>5} {'HP left':>7}")
    for gate in eng.gate_registry.all():
        level = max(1, gate.min_level + args.offset)
        if level < entry_level(gate):                   # the Registry wouldn't let this player in
            print(f"{gate.name[:42]:<42} {level:>3} {gate.party_size:>5}     -  locked until Lv {entry_level(gate)}")
            continue
        wins, left = 0, 0.0
        for _ in range(args.runs):
            random.seed(rng.random())
            ok, hp = run_gate(gate, level, args.cls, regs, rng, args.build)
            wins += ok
            left += hp
        rate = wins / args.runs
        avg = left / wins if wins else 0.0
        note = "  << too hard" if rate < 0.4 else ("  << trivial" if rate == 1.0 and avg > 0.75 else "")
        flagged += bool(note)
        print(f"{gate.name[:42]:<42} {level:>3} {gate.party_size:>5} {rate * 100:>4.0f}% {avg * 100:>6.0f}%{note}")
    print(f"\n{flagged} Gate(s) flagged for a level-{args.offset:+d} {args.cls}.")


if __name__ == "__main__":
    main()
