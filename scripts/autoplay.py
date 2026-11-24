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

Two players (``--mode``):

- ``goal`` (default) plays like someone trying to progress: rests when hurt,
  hands in finished quests, picks up work from the journal's leads, runs the
  Gates the Registry opens for its level (with a companion when one is about),
  and otherwise fights the repeatable dungeon encounters its level suits. It
  walks there over the scene graph, uses its best damage skill, buys potions,
  spends stat points on its main stat and commits no crimes.
- ``random`` takes scene choices at random, sometimes opens a menu, and backs
  out of menus and loops: good at finding crashes, useless for pacing.

Crashes are caught and reported with their traceback.
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


def _is_back(title: str) -> bool:
    """A way out of a menu: '← Back', 'Farewell.', 'Leave the inn.' (not 'Backstab')."""
    import re
    t = re.sub(r"^\d+\.\s*", "", title.strip().lower())
    return t.startswith("←") or t.split(" ")[0].rstrip(".") in ("back", "leave", "farewell", "return", "done", "close")


class _Stop(BaseException):
    """The step budget ran out. BaseException, so the game's own error
    handling (which logs and carries on) can't swallow it as a crash."""


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
        backs = [c for c, t in zip(live, titles) if _is_back(t)]

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


# ── Goal-directed player ─────────────────────────────────────────────────────

GRIND = (                     # (min level, encounter): fights to take when nothing else waits
    (1, "combat:wolf_pack"),      # repeatable once the goblins are gone
    (1, "combat:goblin_patrol"),  # once
    (3, "combat:troll_patrol"),   # repeatable
)
MAX_ACTIVE_QUESTS = 4
RANDOM_SCENES = ("character_creation", "prologue")   # keep class / background variety
WALK_LIMIT = 20               # choices spent walking to a goal before giving it up for a while
CRIME_WORDS = ("turn yourself in", "lift a purse", "palm the", "burgle")


def scene_graph() -> dict:
    """{(scene, node): [(option_id, (scene, node), triggers)]} from the scene JSON."""
    import json
    graph: dict = {}
    for f in sorted((ROOT / "scenes" / "data").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for node_id, node in (data.get("nodes") or {}).items():
            edges = []
            for opt in node.get("options", []):
                target = opt.get("leads_to") or "__stay__"
                tscene = f.stem if target == "__stay__" else target
                edges.append((opt.get("option_id"), (tscene, opt.get("leads_to_node") or "root"),
                              list(opt.get("triggers") or [])))
            graph[(f.stem, node_id)] = edges
    return graph


def scene_locks() -> dict:
    """{(scene, option_id): (flags it needs, flags_any)} from the scene JSON."""
    import json
    locks = {}
    for f in sorted((ROOT / "scenes" / "data").glob("*.json")):
        for node in (json.loads(f.read_text(encoding="utf-8")).get("nodes") or {}).values():
            for opt in node.get("options", []):
                req = opt.get("requires") or {}
                if req.get("flags") or req.get("flags_any"):
                    locks[(f.stem, opt.get("option_id"))] = (list(req.get("flags") or []),
                                                             list(req.get("flags_any") or []))
    return locks


def lair_clear_flags() -> dict:
    """{flag: lair_id} for the dungeon lairs' clear_flags (beating the lair sets them)."""
    import json
    out = {}
    for f in sorted((ROOT / "data" / "dungeons").glob("*.json")):
        for lair in json.loads(f.read_text(encoding="utf-8")).get("lairs", []):
            for flag in lair.get("clear_flags") or []:
                out[flag] = lair["lair_id"]
    return out


def distances(graph: dict, targets: set) -> dict:
    """Steps from every node to the nearest node in ``targets``."""
    rev: dict = {}
    for src, edges in graph.items():
        for _oid, dst, _t in edges:
            rev.setdefault(dst, set()).add(src)
    dist = {t: 0 for t in targets}
    todo = list(targets)
    while todo:
        nxt = []
        for node in todo:
            for src in rev.get(node, ()):
                if src not in dist:
                    dist[src] = dist[node] + 1
                    nxt.append(src)
        todo = nxt
    return dist


class GoalBackend(AutoBackend):
    """Plays to progress (see the module docstring). Falls back to the random
    player's answers for anything it has no rule for."""

    def __init__(self, rng, steps, name) -> None:
        super().__init__(rng, steps, name)
        self.engine = None
        self.graph = scene_graph()
        self.locks = scene_locks()
        self.lair_flags = lair_clear_flags()   # flag -> lair whose fight sets it
        self.key = None                        # (goal, key predicate, give up at prompt n)
        self.blocked: dict = {}           # goal key -> prompt number it may be tried again
        self.goal_seen: Counter = Counter()
        self.gate_tries: Counter = Counter()
        self.lair_tries: Counter = Counter()
        self.goals: Counter = Counter()   # what it spent its scene choices on
        self.walking = ("", 0)            # (goal, choices spent walking to it in a row)
        self.said: Counter = Counter()    # dialogue lines already picked

    @property
    def _player(self):
        st = getattr(self.engine, "state", None)
        return getattr(st, "player", None) if st else None

    def _hp(self) -> float:
        p = self._player
        return p.current_hp / max(1, p.max_hp) if p else 1.0

    def _nodes_with(self, pred) -> set:
        return {node for node, edges in self.graph.items() if any(pred(oid, t) for oid, _d, t in edges)}

    @staticmethod
    def _talk(npc_id: str):
        return lambda _oid, t: f"talk_npc:{npc_id}" in t

    def _goals(self) -> list:
        """Ordered (key, option predicate): the first reachable one wins."""
        e, p = self.engine, self._player
        out = []
        if self._hp() < 0.45:
            out.append(("rest", lambda _o, t: any(x.startswith("rest_camp:full") for x in t)))
        if e.quest_registry is not None and e.npc_registry is not None and e.state.world_db is not None:
            from systems import quest_system as qs
            talkable = {x[9:] for edges in self.graph.values() for _o, _d, t in edges for x in t
                        if x.startswith("talk_npc:")}
            for npc_id in sorted(talkable):
                if qs.reportable_quests(npc_id, e.state, e.quest_registry):
                    out.append((f"report:{npc_id}", self._talk(npc_id)))
            if len(p.active_quest_ids) < MAX_ACTIVE_QUESTS:
                for npc, _tmpl in qs.available_work(e.state, e.quest_registry, e.npc_registry):
                    if npc.npc_id in talkable and f"work:{npc.npc_id}" not in self.blocked:
                        out.append((f"work:{npc.npc_id}", self._talk(npc.npc_id)))
                        break
        if self._hp() >= 0.85 and getattr(e, "lair_registry", None) is not None:
            from core.lair_flow import get_state
            from entities.gate import GATE_ZONE_PREFIX
            # The dungeon's lairs in order: the next one waits until the last is beaten.
            for lair in sorted(e.lair_registry.all(), key=lambda l: l.min_level):
                if lair.zone_id.startswith(GATE_ZONE_PREFIX) or p.level < lair.min_level + 1:
                    continue
                st = get_state(e, lair.lair_id)
                if st is not None and st.occupied and self.lair_tries[lair.lair_id] < 4:
                    out.append((f"lair:{lair.lair_id}",
                                lambda _o, t, lid=lair.lair_id: f"boss_lair:{lid}" in t))
                if not all(p.has_flag(f) for f in lair.clear_flags):
                    break
        if self._hp() >= 0.8 and self._gate_to_run() is not None:
            out.append(("gate", lambda _o, t: "gate_registry" in t))
        for lvl, trig in reversed(GRIND):
            if p.level >= lvl:
                out.append((f"grind:{trig[7:]}", lambda _o, t, trig=trig: trig in t))
        return out

    def _gate_to_run(self):
        """The Gate it would pick at the Registry: open to its level, not tried
        too often; the lowest uncleared first, else its highest cleared one."""
        e, p = self.engine, self._player
        if getattr(e, "gate_registry", None) is None:
            return None
        from core.gate_flow import SOLO_OVERRIDE_LEVELS, entry_level
        ok = [g for g in e.gate_registry.all()
              if p.level >= entry_level(g) and self.gate_tries[g.gate_id] < 3
              and (g.party_size <= 1 or p.level >= g.min_level + SOLO_OVERRIDE_LEVELS)]
        fresh = sorted((g for g in ok if not p.has_flag(g.clear_flag)), key=lambda g: g.min_level)
        if fresh:
            return fresh[0]
        done = sorted(ok, key=lambda g: -g.min_level)
        return done[0] if done and p.level <= done[0].min_level + 2 else None

    def select(self, message, choices, default=None):
        st = getattr(self.engine, "state", None)
        if (self._player is None or getattr(st, "current_scene_id", None) in (None, *RANDOM_SCENES)):
            return super().select(message, choices, default)
        live = [c for c in choices if not c.disabled]
        titles = [str(c.title) for c in live]
        try:
            pick = self._decide(message, live, titles, [t.lower() for t in titles])
        except Exception:
            pick = None
        if pick is None:
            return super().select(message, choices, default)
        self._tick()
        self.seen[(message, tuple(t.lower() for t in titles))] += 1
        return pick.value

    @staticmethod
    def _first(live, low, *needles):
        for c, t in zip(live, low):
            if any(n in t for n in needles):
                return c
        return None

    def _decide(self, message, live, titles, low):
        p = self._player
        if message == "Choose:":
            return self._scene(live, titles)
        if message == "Your action:":
            return self._combat(live, titles)
        if message == "Target:":
            return min(zip(live, titles), key=lambda ct: int(ct[1].split("(")[-1].split("/")[0]))[0]
        if message == "Allocate stat point:":
            main = max(("STR", "INT", "AGI"), key=lambda s: getattr(p.stats, s, 0))
            return self._first(live, low, ("vit" if self.rng.random() < 0.25 else main.lower()))
        if message == "Say:":
            return self._say(live, low)
        if message.endswith("'s wares:"):
            potions = sum(s.quantity for s in p.inventory
                          if "potion" in s.item_id and "mana" not in s.item_id)
            buy = self._first(live, low, "health potion")
            if buy is not None and potions < 3 and p.gold >= 3000:
                return buy
            return self._first(live, low, "done")
        if message.startswith("Sell to "):
            return self._first(live, low, "done")
        if message == "Which Gate?":
            gate = self._gate_to_run()
            for c in live:
                if gate is not None and c.value == gate.gate_id:
                    self.gate_tries[gate.gate_id] += 1
                    return c
            return self._first(live, low, "leave")
        if message == "Enter it?":
            return live[0] if self._hp() >= 0.7 else live[-1]
        if message.startswith("HP ") and any("press on" in t for t in low):
            return self._first(live, low, "press on" if self._hp() >= 0.4 else "withdraw")
        if message == "Take a companion?" or message.startswith("Group Gate"):
            return self._first(live, low, "bring ") or self._first(live, low, "set off", "go alone")
        if message == "Anyone else?":
            return self._first(live, low, "set off", "go alone")
        if message == "What do you do?":
            if self._first(live, low, "study") is not None:
                return self._first(live, low, "study")
            return self._first(live, low, "fight" if self._hp() >= 0.6 else "retreat")
        if message == "The fight is over. What now?":
            return live[0]
        return None

    def _scene(self, live, titles):
        from core.input_handler import get_current_options
        e = self.engine
        options = get_current_options(e)
        here = (e.state.current_scene_id, e.state.current_node_id)
        by_choice = []                            # (Choice, SceneOption); Choice isn't hashable
        for c, t in zip(live, titles):
            head = t.split(".", 1)[0]
            if head.isdigit() and 0 < int(head) <= len(options):
                by_choice.append((c, options[int(head) - 1]))
        safe = [(c, o) for c, o in by_choice
                if not any(x.startswith(("crime:", "ledger_surrender")) for x in (o.triggers or []))
                and not any(w in o.label.lower() for w in CRIME_WORDS)]
        self.blocked = {k: v for k, v in self.blocked.items() if v > self.prompts}
        for key, pred in self._goals():
            if key in self.blocked:
                continue
            now = [c for c, o in safe if pred(o.option_id, o.triggers or [])]
            if now:
                self.goals[key] += 1
                self.goal_seen.clear()
                if key.startswith("work:"):         # one visit per lead; don't nag
                    self.blocked[key] = self.prompts + 300
                self.walking = ("", 0)
                if key.startswith("lair:"):
                    self.lair_tries[key[5:]] += 1
                return now[0]
            key_step = self._follow_key(key, safe, here)
            if key_step is not None:
                best = key_step
            else:
                dist = distances(self.graph, self._nodes_with(pred))
                if here not in dist:
                    continue
                best, best_d = self._toward(dist, safe, here)
                key_step = self._key_for_lock(key, dist, options, here, best_d, safe)
                if key_step is not None:              # a locked door is the way: find its key
                    best = key_step
            if best is None:
                continue
            self.goal_seen[(key, here)] += 1
            if self.goal_seen[(key, here)] > 4:     # going round in circles: try something else
                self.blocked[key] = self.prompts + 60
                self.goal_seen.clear()
                continue
            walk = self.walking[1] + 1 if self.walking[0] == key else 1
            self.walking = (key, walk)
            if walk > WALK_LIMIT:                     # can't get there (a locked gate): later
                self.blocked[key] = self.prompts + 400
                self.walking = ("", 0)
                continue
            self.goals[key] += 1
            return best
        self.goals["explore"] += 1
        pool = [c for c, _o in (safe or by_choice)]
        return self.rng.choice(pool) if pool else None

    @staticmethod
    def _dest(o, here):
        tscene = here[0] if o.leads_to in (None, "", "__stay__") else o.leads_to
        return (tscene, o.leads_to_node or "root")

    def _toward(self, dist, safe, here):
        """The live choice that gets nearest the goal (None if none gets closer)."""
        best, best_d = None, dist.get(here, 10 ** 6)
        for c, o in safe:
            d = dist.get(self._dest(o, here))
            if d is not None and d < best_d:
                best, best_d = c, d
        return best, best_d

    def _follow_key(self, goal, safe, here):
        """Keep walking to the key found for ``goal`` (see _key_for_lock)."""
        if self.key is None or self.key[0] != goal or self.key[2] <= self.prompts:
            return None
        key_pred = self.key[1]
        now = [c for c, so in safe if key_pred(so.option_id, so.triggers or [])]
        if now:
            self.key = None                           # got it: back to the goal itself
            return now[0]
        step, _d = self._toward(distances(self.graph, self._nodes_with(key_pred)), safe, here)
        if step is None:
            self.key = None
        return step

    def _key_for_lock(self, goal, dist, options, here, best_d, safe):
        """If a locked option here would get closer than any open one, head for
        an option that sets a flag it needs (what a player reads in the lock
        reason). Returns the choice to take, or None."""
        p = self._player
        for o in options:
            if not getattr(o, "locked", False):
                continue
            d = dist.get(self._dest(o, here))
            if d is None or d >= best_d:
                continue
            need_all, need_any = self.locks.get((here[0], o.option_id), ([], []))
            missing = [f for f in need_all if not p.has_flag(f)]
            if need_any and not any(p.has_flag(f) for f in need_any):
                missing += need_any
            if not missing:
                continue
            keys = {f"flag:{f}" for f in missing}
            keys |= {f"boss_lair:{self.lair_flags[f]}" for f in missing if f in self.lair_flags}
            key_pred = lambda _o, t, keys=keys: any(x in keys for x in t)
            self.key = (goal, key_pred, self.prompts + 80)
            step = self._follow_key(goal, safe, here)
            if step is not None:
                return step
        return None

    def _combat(self, live, titles):
        e, p = self.engine, self._player
        by_name = {}
        for sid in p.skills:
            sk = e.skill_registry.get(sid)
            if sk is not None:
                by_name[sk.name] = sk
        best, best_v = None, 0.0
        for c, t in zip(live, titles):
            if "[CD" in t:
                continue
            sk = by_name.get(t.split(" [")[0])
            if sk is None or sk.mp_cost > p.current_mp:
                continue
            kinds = {getattr(ef.effect_type, "value", ef.effect_type) for ef in sk.effects}
            if "heal" in kinds:
                if self._hp() < 0.4:
                    return c
                continue
            v = sum(ef.base_value + ef.scaling_coefficient
                    * getattr(p.stats, (ef.scaling_stat or "STR").upper(), 0) for ef in sk.effects)
            if kinds & {"damage", "drain"} and v > best_v:
                best, best_v = c, v
        basic = self._first(live, [t.lower() for t in titles], "basic attack")
        return best if best is not None and best_v >= max(p.stats.STR, p.stats.AGI) + 5 else basic

    def _say(self, live, low):
        rep = self._first(live, low, "« report:")
        if rep is not None:
            return rep
        e = self.engine
        if len(self._player.active_quest_ids) < MAX_ACTIVE_QUESTS and e.quest_registry is not None:
            from systems import quest_system as qs
            titles = {f"« {t.title.lower()} »" for _n, t in
                      qs.available_work(e.state, e.quest_registry, e.npc_registry)}
            for c, t in zip(live, low):
                if t.split(". ", 1)[-1] in titles:
                    return c
        # Authored dialogue can offer work too: try each line once, then leave.
        bye = self._first(live, low, "farewell", "goodbye", "that's all", "nothing today", "leave", "← back")
        fresh = [c for c, t in zip(live, low) if c is not bye and self.said[t] == 0
                 and not any(w in t for w in ("buy", "sell", "browse", "« ", "team up", "warrant", "bribe"))]
        if fresh:
            self.said[str(fresh[0].title).lower()] += 1
            return fresh[0]
        return bye


def play(seed: int, steps: int, mode: str = "goal") -> dict:
    from config import SAVES_DIR
    from ui import prompt as uip
    from ui import renderer
    from persistence import save_manager

    rng = random.Random(seed)
    name = f"Autoplay {seed}"
    slot = f"autoplay_{seed}"
    for f in (f"{slot}.json", f"{slot}.db", f"{slot}.db-wal", f"{slot}.db-shm"):
        (SAVES_DIR / f).unlink(missing_ok=True)     # our own last run: start in a fresh world
    backend = (GoalBackend if mode == "goal" else AutoBackend)(rng, steps, name)
    uip.set_backend(backend)
    real_file = renderer.console.file
    renderer.console.file = io.StringIO()                       # silence the screen
    engine = None
    crash = ""
    try:
        from core.game_engine import GameEngine
        engine = GameEngine()
        backend.engine = engine
        engine.run()
    except (_Stop, SystemExit):
        pass
    except Exception:
        crash = traceback.format_exc()
    finally:
        renderer.console.file = real_file
        uip.set_backend(None)

    out = {"seed": seed, "prompts": backend.prompts, "crash": crash,
           "goals": dict(getattr(backend, "goals", {}))}
    if backend.seen:
        (msg, titles), n = backend.seen.most_common(1)[0]
        out["stuck"] = (n, msg, list(titles)[:6])
    state = getattr(engine, "state", None) if engine else None
    if state is not None and state.player is not None:
        p = state.player
        try:
            save_manager.save_game(state, state.slot_name or slot, SAVES_DIR)
        except Exception:
            out["crash"] = out["crash"] or traceback.format_exc()
        out.update(level=p.level, gold=p.gold, turn=state.turn_number, scene=state.current_scene_id,
                   lives=p.lives_remaining, cls=p.active_class or p.base_class or "unclassed",
                   items=sum(s.quantity for s in p.inventory), skills=len(p.skills),
                   gates=sorted(k[8:] for k in p.flags if k.startswith("cleared_")),
                   quests=len(p.active_quest_ids), done=len(p.completed_quest_ids),
                   heat=round(getattr(p, "heat", 0)), crimes=dict(getattr(p, "crimes", {})))
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
    ap.add_argument("--mode", choices=("goal", "random"), default="goal")
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
        r = play(args.seed + i, args.steps, args.mode)
        if r.get("crash"):
            crashes += 1
            print(f"run {r['seed']}: CRASH after {r['prompts']} prompts\n{r['crash']}")
            continue
        print(f"run {r['seed']}: {r['prompts']} prompts · level {r.get('level')} {r.get('cls')} · "
              f"turn {r.get('turn')} · at {r.get('scene')} · lives {r.get('lives')} · "
              f"gold {r.get('gold', 0) // 100}g · {r.get('items')} items · {r.get('skills')} skills · "
              f"quests {r.get('quests')} active / {r.get('done')} done · gates cleared {r.get('gates')} · "
              f"heat {r.get('heat')} crimes {r.get('crimes') or '-'}")
        if r.get("goals"):
            top = sorted(r["goals"].items(), key=lambda kv: -kv[1])[:6]
            print("   spent choices on: " + ", ".join(f"{k} {v}" for k, v in top))
        if r.get("stuck") and r["stuck"][0] > max(25, r["prompts"] // 10):
            n, msg, titles = r["stuck"]
            print(f"   most repeated prompt ({n}×): {msg!r} {titles}")
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
