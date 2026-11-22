#!/usr/bin/env python3
"""Content health: unreachable scenes/nodes, dead links, quests nobody offers,
items that never appear, materials nothing uses. Exit code 1 if any.

    python scripts/content_health.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from core.bootstrap import load_registries
    from core.content_health import check
    from core.game_engine import GameEngine
    engine = GameEngine()
    load_registries(engine)
    report = check(engine)
    print("\n".join(report.lines(limit=50)))
    print(f"\n{report.problems()} problem(s).")
    return 1 if report.problems() else 0


if __name__ == "__main__":
    sys.exit(main())
