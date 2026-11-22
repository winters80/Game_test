#!/usr/bin/env python3
"""
Autoplay: let a scripted player play the real game headlessly, to find
crashes, dead ends and balance problems, and to feed the playtest report.

    python scripts/autoplay.py                 # 1 run of 600 choices
    python scripts/autoplay.py --runs 5 --steps 1500 --seed 7

Each run starts a new game, plays it through the real engine (offline: no
AI), and saves it as ``autoplay_<seed>``. Then it prints what happened and
writes logs/playtest_report.md (scripts/playtest_report.py) over all saves.
Every run also gets a diagnostics log in logs/sessions/.

The player mostly takes scene choices, fights rather than flees, sometimes
opens a menu (journal, items, who's around, the World Log), and backs out of
menus and loops. Crashes are caught and reported with their traceback.
"""
from __future__ import annotations

import argparse
import io
import random
import sys
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AVOID = ("quit", "admin", "[s]", "[a]", "[q]", "[?]", "[g]", "delete", "drop", "abandon",
         "betray", "load game", "save")
BACK = ("← back", "← close", "← done", "← cancel", "leave", "farewell", "back", "return")
PEEK = ("[j]", "[i]", "[w]", "[l]", "[k]", "[c]")


class _Stop(Exception):
    pass


class AutoBackend:
    """A ui/prompt backend that answers like a curious, persistent player."""

    def __init__(self, rng: random.Random, steps: int, name: str) -> None:
        self.rng, self.left, self.name = rng, steps, name
        self.prompts = 0
        self.seen: Counter = Counter()
        self.menu_depth = 0
        self.scenes: Counter = Counter()

    def _tick(self) -> None:
        self.left -= 1
        self.prompts += 1
        if self.left <= 0:
            raise _Stop()

    def select(self, message, choices, default=None):
        self._tick()
        live = [c for c in choices if not c.disabled]
        if not live:
            return None
        titles = [str(c.title).lower() for c in live]
        key = (message, tuple(titles))
        self.seen[key] += 1
        safe = [c for c, t in zip(live, titles) if not any(a in t for a in AVOID)]
        backs = [c for c, t in zip(live, titles) if any(t.startswith(b) or b in t for b in BACK)]

        if message.startswith("Your action"):                    # combat: fight
            fight = [c for c, t in zip(live, titles) if "flee" not in t and "item" not in t]
            return self.rng.choice(fight or live).value
        if "new game" in titles:
            return live[titles.index("new game")].value
        if message == "Choose:":                                 # a scene
            self.menu_depth = 0
            scene_opts = [c for c, t in zip(live, titles) if t[:1].isdigit()]
            peeks = [c for c, t in zip(live, titles) if t.startswith(PEEK)]
            # Stuck on the same screen? Take a different numbered choice.
            if peeks and self.rng.random() < 0.08:
                return self.rng.choice(peeks).value
            if scene_opts:
                if self.seen[key] > 6:
                    return self.rng.choice(scene_opts).value
                # Prefer choices not taken yet from this screen.
                return self.rng.choice(scene_opts).value
            return self.rng.choice(safe or live).value
        # Menus, dialogue, shops: look around a little, then leave.
        self.menu_depth += 1
        if backs and (self.menu_depth > 3 or self.seen[key] > 2 or self.rng.random() < 0.35):
            return backs[0].value
        pool = [c for c in (safe or live) if c not in backs] or live
        return self.rng.choice(pool).value

    def text(self, message, default="", instruction=None):
        self._tick()
        if "name" in message.lower():
            return self.name
        return default or "1"

    def confirm(self, message, default=True):
        self._tick()
        if "download" in message.lower():
            return False
        return default

    def any_key(self, message):
        self._tick()

    # renderer hooks: nothing to draw
    def any_key_hook(self):
        self._tick()


def play(seed: int, steps: int) -> dict:
    from config import SAVES_DIR
    from ui import prompt as uip
    from ui import renderer
    from persistence import save_manager

    rng = random.Random(seed)
    name = f"Autoplay {seed}"
    backend = AutoBackend(rng, steps, name)
    uip.set_backend(backend)
    real_file = renderer.console.file
    renderer.console.file = io.StringIO()                       # silence the screen
    engine = None
    crash = ""
    try:
        from core.game_engine import GameEngine
        engine = GameEngine()
        engine.run()
    except (_Stop, SystemExit):
        pass
    except Exception:
        crash = traceback.format_exc()
    finally:
        renderer.console.file = real_file
        uip.set_backend(None)

    out = {"seed": seed, "prompts": backend.prompts, "crash": crash}
    state = getattr(engine, "state", None) if engine else None
    if state is not None and state.player is not None:
        p = state.player
        slot = f"autoplay_{seed}"
        try:
            save_manager.save_game(state, slot, SAVES_DIR)
        except Exception:
            out["crash"] = out["crash"] or traceback.format_exc()
        out.update(level=p.level, gold=p.gold, turn=state.turn_number, scene=state.current_scene_id,
                   lives=p.lives_remaining, cls=p.active_class or p.base_class or "unclassed",
                   items=sum(s.quantity for s in p.inventory), skills=len(p.skills),
                   gates=sorted(k[8:] for k in p.flags if k.startswith("cleared_")),
                   quests=len(p.active_quest_ids), done=len(p.completed_quest_ids))
        try:
            save_manager.close_game(state)
        except Exception:
            pass
    if engine is not None and getattr(engine, "ai_service", None):
        engine.ai_service.shutdown()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    import os
    os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:9")   # offline, deterministic
    from utils.logging_setup import setup_logging
    setup_logging()
    import logging
    for h in list(logging.getLogger().handlers):                      # keep the terminal clean
        if type(h) is logging.StreamHandler:
            logging.getLogger().removeHandler(h)
    from utils import session_log
    session_log.start_if_enabled()

    crashes = 0
    for i in range(args.runs):
        r = play(args.seed + i, args.steps)
        if r.get("crash"):
            crashes += 1
            print(f"run {r['seed']}: CRASH after {r['prompts']} prompts\n{r['crash']}")
            continue
        print(f"run {r['seed']}: {r['prompts']} prompts · level {r.get('level')} {r.get('cls')} · "
              f"turn {r.get('turn')} · at {r.get('scene')} · lives {r.get('lives')} · "
              f"gold {r.get('gold', 0) // 100}g · {r.get('items')} items · {r.get('skills')} skills · "
              f"quests {r.get('quests')} active / {r.get('done')} done · gates cleared {r.get('gates')}")
    session_log.stop()
    try:
        import scripts.playtest_report as pr
        from config import SAVES_DIR
        text = pr.build_report(SAVES_DIR, ROOT / "logs")
        (ROOT / "logs" / "playtest_report.md").write_text(text, encoding="utf-8")
        print(f"\nPlaytest report: {ROOT / 'logs' / 'playtest_report.md'}")
    except Exception:
        traceback.print_exc()
    return 1 if crashes else 0


if __name__ == "__main__":
    sys.exit(main())
