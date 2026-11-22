#!/usr/bin/env python3
"""
SYSTEM BREAKER — LitRPG Text Quest
Entry point.
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    # Initialise logging before anything else
    from utils.logging_setup import setup_logging
    setup_logging()
    from utils import session_log
    session_log.start_if_enabled()      # diagnostics transcript (toggle in Admin)

    log = logging.getLogger("core.main")
    log.info("=" * 60)
    log.info("SYSTEM BREAKER starting up")

    if "--web" in sys.argv:              # the Textual app, in a browser
        from ui.tui_app import run_web
        run_web()
        return
    if "--tui" in sys.argv:              # the Textual app, in this terminal
        from ui.tui_app import run_tui
        log.info("Starting the Textual interface")
        run_tui()
        return

    try:
        from core.game_engine import GameEngine
        engine = GameEngine()
        log.info("Engine bootstrapped, entering run()")
        engine.run()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — clean exit")
        print("\n\nFarewell, adventurer.")
        sys.exit(0)
    except Exception:
        log.exception("Unhandled exception in main()")
        raise


if __name__ == "__main__":
    main()
