"""
The Ledger: crimes, heat, wardens, bot crimes and arrests, and the tribunal
before the Break.

    python tests/test_law.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:9")

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓  " if cond else "  ✗  ") + msg)
    if not cond:
        failures.append(msg)


class Always:
    """rng stand-in: random() always returns ``v``."""
    def __init__(self, v: float):
        self.v = v

    def random(self):
        return self.v

    def randint(self, a, b):
        return a


def make_engine(tmp: Path):
    from core.bootstrap import load_registries
    from core.game_engine import GameEngine
    from entities.player import Player
    from persistence.save_manager import new_game_state
    eng = GameEngine()
    load_registries(eng)
    eng.state = new_game_state(Player(name="Lawtest"), tmp, "lawtest")
    eng.state.item_registry = eng.item_registry
    eng._ai_online = lambda: False
    return eng


def rules() -> None:
    from systems import law_system as law
    from core.state_manager import GameState
    from entities.player import Player
    st = GameState(Player(name="P"))
    st.current_scene_id = "verath_city"
    r = law.commit_crime(st, "theft", rng=Always(0.0))
    check(r.witnessed and st.player.heat == 15 and st.player.bounty == 1500 and st.player.crimes["theft"] == 1,
          "a witnessed theft adds heat, a bounty and a record")
    r = law.commit_crime(st, "theft", rng=Always(0.99))
    check(not r.witnessed and st.player.heat == 15 + law.SUSPICION, "an unseen theft only adds suspicion")
    st.current_scene_id = "dungeon_floor2"
    r = law.commit_crime(st, "burglary", rng=Always(0.0))
    check(not r.witnessed, "no wardens in the dungeon")
    check(law.commit_crime(st, "jaywalking") is None, "unknown crimes are ignored")
    st.player.stats.AGI, st.player.stats.LCK = 15, 15
    check(abs(law.stealth(st.player) - 0.45) < 1e-9, "stealth is capped at 45% (AGI + LCK)")
    h = st.player.heat
    law.cool_down(st.player, 12)
    check(abs(st.player.heat - (h - 2)) < 1e-9 and st.player.bounty == 1500, "heat cools 1 per hour; the bounty stays")
    st.player.set_flag("breakers_assembled")
    first = law.check_sedition(st)
    again = law.check_sedition(st)
    check(len(first) == 1 and first[0].witnessed and not again, "the System reports sedition, once per milestone")
    check(law.fine_for(st.player, 50) < law.fine_for(st.player, 0), "Ledger deputies pay half the fine")
    st.player.heat = 80
    st.current_scene_id = "verath_city"
    check(law.enforcement_due(st, Always(0.0)) == "patrol", "wanted in town: a patrol stops you")
    st.current_scene_id = "camp_rest"
    check(law.enforcement_due(st, Always(0.0)) == "hunters", "hunted anywhere: hunters come")
    st.player.heat = 5
    st.current_scene_id = "verath_city"
    check(law.enforcement_due(st, Always(0.0)) == "", "a clean-ish record is left alone")
    check(law.crime_for_action("steal") == "theft" and law.crime_for_action("loot") == "" and
          law.crime_for_action("burgle") == "burglary", "typed [?] actions map to crimes (looting isn't one)")


def triggers_and_flow(tmp: Path) -> None:
    from core import law_flow
    from core.event_bus import bus
    from scenes.option_logic import process_triggers
    from ui import prompt as uip
    eng = make_engine(tmp)
    p = eng.state.player
    eng.state.current_scene_id = "verath_city"
    seen = []
    bus.subscribe("CRIME_COMMITTED", lambda ev: seen.append(ev.data))
    with mock.patch("random.random", lambda: 0.0):
        process_triggers(["crime:theft", "give_gold:400"], eng.state)
    bus.flush()
    check(p.heat == 15 and seen and seen[-1]["witnessed"], "the crime: trigger commits the crime and announces it")
    process_triggers(["heat:-30"], eng.state)
    check(p.heat == 0, "heat:-N lowers heat (never below 0)")
    standing0 = eng.state.world_db.get_faction_standing("the_ledger")[0]
    p.heat, p.bounty = 40, 2000
    check(standing0 < 0, "witnessed crimes lower Ledger standing")

    class Pick:
        def __init__(self, value):
            self.value = value

        def select(self, m, choices, default=None):
            for c in choices:
                if c.value == self.value and not c.disabled:
                    return c.value
            return "jail"

        def confirm(self, m, default=True):
            return True

        def text(self, m, default=""):
            return default

        def any_key(self, m):
            pass

        def any_key_hook(self):
            pass

    renderer_quiet = mock.patch("ui.renderer.prompt_any_key", lambda: None)
    renderer_quiet.start()
    try:
        p.gold = 100_000
        uip.set_backend(Pick("pay"))
        r = law_flow.confront(eng, "patrol")
        check(r == "paid" and p.heat == 0 and p.bounty == 0 and p.gold < 100_000, "paying the fine settles the record")
        p.heat, p.bounty = 50, 3000
        p.add_item("stolen_jewellery", 2)
        t0 = eng.state.turn_number
        uip.set_backend(Pick("jail"))
        r = law_flow.confront(eng, "patrol")
        check(r == "jailed" and p.heat == 0 and not p.has_item("stolen_jewellery") and eng.state.turn_number > t0,
              "going quietly: time in the cells, stolen goods confiscated, record settled")
        p.heat = 50
        uip.set_backend(Pick("run"))
        r = law_flow.confront(eng, "patrol", rng=Always(0.0))
        check(r == "escaped" and p.heat == 55, "running away works sometimes, and adds heat")
        p.heat = 50
        uip.set_backend(Pick("fight"))
        with mock.patch.object(eng, "_run_combat", lambda *a, **k: "victory"):
            r = law_flow.confront(eng, "patrol")
        check(r == "fought" and p.crimes.get("assault", 0) >= 1, "beating the wardens is an assault on the Ledger")
        hp_before = p.current_hp
        with mock.patch.object(eng, "_run_combat", lambda *a, **k: "defeat"):
            r = law_flow.confront(eng, "patrol")
        check(r == "beaten" and p.lives_remaining == 9 and p.heat == 0, "losing to wardens means the cells, not a life")
        uip.set_backend(Pick("talk"))
        p.heat = 30
        r = law_flow.confront(eng, "hunters", rng=Always(0.0))
        check(r != "talked", "hunters don't negotiate")
        wardens = law_flow._wardens(10, "hunters")
        check(len(wardens) == 3 and wardens[0].name == "Ledger Hunter" and wardens[0].max_hp > 50,
              "wardens are built for the player's level")

        # Bots: crimes, arrests, warrants
        from systems.bot_system import BotAgent
        from systems import bot_brain as bb
        crook = BotAgent(bot_id="crook", name="Crook", archetype="trader", level=3, current_zone_id="verath_city",
                         allegiance="outlaw")
        dep = BotAgent(bot_id="dep", name="Deputy", archetype="fighter", level=9, current_zone_id="verath_city",
                       allegiance="ledger")
        world = bb.BotWorld(eng.item_registry, lambda z: [], [], {}, 100, allies={"verath_city": [crook, dep]})
        g0 = crook.gold
        ev = bb._bot_crime(crook, world, Always(0.0))
        check(crook.gold > g0 and crook.heat == 15 and ev and ev[0].kind == "crime", "outlaw bots pick pockets and get seen")
        crook.heat = 40
        ev = bb._bot_patrol(dep, world, Always(0.0))
        check(ev and ev[0].kind == "arrest" and crook.jailed_until > 100 and crook.heat == 0,
              "Ledger bots arrest wanted bots")
        check(bb.tick_bot(crook, world, random.Random(1)) == [] and crook.activity == "in the Ledger's cells",
              "a jailed bot sits in the cells")
        from systems.bot_chat import party_refusal
        check("warrant" in party_refusal(dep, None, 5, player_heat=40), "a Ledger deputy won't team up with a wanted player")

        mgr = eng._bot_manager
        crook.jailed_until, crook.heat = 0, 50
        p.heat, p.bounty = 0, 0
        eng.state.world_db.update_faction_standing("the_ledger", 30)
        with mock.patch.object(mgr, "all", lambda: [crook]):
            check([b.bot_id for b in law_flow.wanted_bots(eng)] == ["crook"], "the warrant board lists wanted bots")
            check(law_flow.can_arrest(eng, crook), "a clean player in good standing can serve a warrant")
            p.level = 10
            r = law_flow.arrest_bot(eng, crook)
        check(r == "arrested" and p.has_flag("ledger_warrant_served") and crook.jailed_until > 0,
              "serving a warrant jails the bot and counts for the Ledger's quest")
    finally:
        renderer_quiet.stop()
        uip.set_backend(None)
        from persistence.save_manager import close_game
        close_game(eng.state)


def content(tmp: Path) -> None:
    import json
    f = json.loads((ROOT / "scenes" / "data" / "dungeon_floor3.json").read_text())
    opts = {o["option_id"]: o for o in f["nodes"]["core_chamber"]["options"]}
    check("ledger_tribunal_broken" in opts["destroy_the_system"]["requires"]["flags"]
          and "boss_lair:ledger_tribunal" in opts["face_ledger_tribunal"]["triggers"],
          "the Break waits on the Ledger's tribunal in the Fracture Core")
    from config import DATA_DIR
    from entities.faction import FactionRegistry
    fr = FactionRegistry()
    fr.load_from_file(DATA_DIR / "factions" / "faction_definitions.json")
    led = fr.get("the_ledger")
    check(led is not None and "system_breakers" in led.rival_factions and led.alignment_min == -100,
          "the Ledger is a neutral faction, rival to the Breakers")
    from systems.bot_system import BotAgent
    from collections import Counter
    c = Counter(BotAgent(bot_id=f"b{i}", name="x").allegiance for i in range(300))
    check(all(c[k] > 10 for k in ("ledger", "breakers", "outlaw", "")), f"bots take sides ({dict(c)})")
    b1, b2 = BotAgent(bot_id="same", name="a"), BotAgent(bot_id="same", name="b")
    check(b1.allegiance == b2.allegiance, "a bot's allegiance is fixed by its id")


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()
    with tempfile.TemporaryDirectory() as tmp:
        print("\n  rules")
        rules()
        print("\n  triggers, encounters, bots")
        triggers_and_flow(Path(tmp))
        print("\n  content")
        content(Path(tmp))
    print()
    if failures:
        print(f"  {len(failures)} law check(s) failed.")
        sys.exit(1)
    print("  All law checks passed.")
