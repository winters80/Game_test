#!/usr/bin/env python3
"""
Apocalypse Breaker — LitRPG Text Quest
Entry point.
"""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from core.game_engine import GameEngine
        engine = GameEngine()
        engine.run()
    except KeyboardInterrupt:
        print("\n\nFarewell, adventurer.")
        sys.exit(0)


if __name__ == "__main__":
    main()
