#!/usr/bin/env python3
"""
Playtest report: what happened across every save on this machine.

    python scripts/playtest_report.py            # writes logs/playtest_report.md
    python scripts/playtest_report.py --print    # and prints it

Reads saves/*.json (the player) and each paired world DB (through
WorldDatabase.insights, never raw SQL), plus the error and AI logs, and
summarises: how far players got, where they died, which bosses fell to whom,
which typed actions grew the world, how often the AI failed. Use it to
decide what to rebalance, and what to keep if the game is rebuilt.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _players(saves: Path) -> list[dict]:
    out = []
    for f in sorted(saves.glob("*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            p = raw.get("player", {})
            flags = p.get("flags", {}) or {}
            out.append({
                "slot": f.stem, "name": p.get("name", "?"), "level": p.get("level", 1),
                "class": p.get("active_class") or p.get("base_class") or "unclassed",
                "turns": raw.get("turn_number", p.get("turn_count", 0)),
                "hours": round((p.get("play_time_seconds", 0) or 0) / 3600, 1),
                "lives_used": p.get("lives_used", 0),
                "gates_cleared": sorted(k[8:] for k in flags if k.startswith("cleared_")),
                "unexpected_choices": sum(1 for c in p.get("choice_history", []) if c.startswith("unexpected:")),
                "class_awakened": bool(flags.get("class_awakened")),
            })
        except Exception as e:
            out.append({"slot": f.stem, "error": str(e)})
    return out


def _db_insights(saves: Path, slot: str) -> dict:
    from persistence.world_db import WorldDatabase
    path = saves / f"{slot}.db"
    if not path.exists():
        return {}
    db = WorldDatabase(path)
    try:
        db.open()
        return db.insights()
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


def _log_counts(logs: Path) -> dict[str, int]:
    counts: Counter = Counter()
    for name in ("error.log", "ai.log", "game.log"):
        f = logs / name
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if " ERROR " in line:
                counts[f"{name}: errors"] += 1
            if "exhausted all" in line:
                counts["AI JSON calls that gave up"] += 1
            if re.search(r"generation failed|Generation failed", line):
                counts["AI generation failures"] += 1
            if "Typed action" in line and "matched existing option" in line:
                counts["[?] typed text matched a choice"] += 1
            if "Dynamic query:" in line:
                counts["[?] questions asked"] += 1
    return dict(counts)


def _table(rows: list[dict], cols: list[str]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return out + [""]


def build_report(saves: Path, logs: Path) -> str:
    players = _players(saves)
    lines = [f"# Playtest report ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"{len(players)} save(s) in `{saves}`.", "", "## Players", ""]
    lines += _table(players, ["slot", "name", "level", "class", "turns", "hours", "lives_used",
                              "unexpected_choices", "class_awakened"])
    deaths: Counter = Counter()
    kills: Counter = Counter()
    actions: Counter = Counter()
    gates: Counter = Counter()
    for p in players:
        for g in p.get("gates_cleared", []):
            gates[g] += 1
        ins = _db_insights(saves, p["slot"])
        for d in ins.get("deaths_by_zone", []):
            deaths[d["zone_id"]] += d["n"]
        for k in ins.get("lair_kills", []):
            kills[(k["occupant_id"], k["killer_kind"])] += k["n"]
        for a in ins.get("world_actions", []):
            actions[a["status"]] += a["n"]
    lines += ["## Where players die", ""]
    lines += _table([{"zone": z, "deaths": n} for z, n in deaths.most_common(15)], ["zone", "deaths"])
    lines += ["## Bosses killed (by player / bot)", ""]
    lines += _table([{"occupant": o, "killer": k, "kills": n} for (o, k), n in kills.most_common(20)],
                    ["occupant", "killer", "kills"])
    lines += ["## Gates cleared (saves that cleared each)", ""]
    lines += _table([{"gate": g, "saves": n} for g, n in gates.most_common(32)], ["gate", "saves"])
    lines += ["## Typed actions that grew the world (by status)", ""]
    lines += _table([{"status": s, "actions": n} for s, n in actions.most_common()], ["status", "actions"])
    lines += ["## Logs", ""]
    lines += _table([{"signal": k, "count": v} for k, v in sorted(_log_counts(logs).items())], ["signal", "count"])
    lines += ["## Reading it", "",
              "- A zone with many deaths at a low average level is over-tuned (or under-signposted).",
              "- Bosses only bots kill are ones players avoid: check their rewards and the lair text.",
              "- Many `no_ai` / `capped` actions: players type more than the AI can grow; raise caps or add yields.",
              "- AI give-ups: try another model (Admin → AI models → Benchmark).", ""]
    return "\n".join(lines)


def main() -> None:
    from config import SAVES_DIR
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    text = build_report(SAVES_DIR, logs)
    out = logs / "playtest_report.md"
    out.write_text(text, encoding="utf-8")
    if "--print" in sys.argv:
        print(text)
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
