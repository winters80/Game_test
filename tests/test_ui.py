"""
The prompt layer (ui/prompt), the diagnostics log (utils/session_log) and the
Textual app (ui/tui_app), driven headlessly with Textual's test pilot.

    python tests/test_ui.py

The Textual part is skipped (with a note) when ``textual`` isn't installed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:9")   # no AI: deterministic

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓  " if cond else "  ✗  ") + msg)
    if not cond:
        failures.append(msg)


# ── ui/prompt ─────────────────────────────────────────────────────────────────

def test_prompt_layer() -> None:
    from ui import prompt as uip

    class Fake:
        def __init__(self):
            self.seen = []

        def select(self, message, choices, default=None):
            self.seen.append((message, [(c.title, c.value, c.disabled) for c in choices]))
            return choices[-1].value

        def text(self, message, default=""):
            return default + "!"

        def confirm(self, message, default=True):
            return not default

        def any_key(self, message):
            self.seen.append(("any", message))

    fake = Fake()
    uip.set_backend(fake)
    try:
        got = uip.select("Pick:", ["a", uip.Choice("Bee", value="b"),
                                   uip.Choice("Sea", disabled="locked")]).ask()
        check(got == "Sea", "select returns the backend's value")
        check(fake.seen[0][1] == [("a", "a", None), ("Bee", "b", None), ("Sea", "Sea", "locked")],
              "plain strings and Choice(title, value, disabled) normalise for the backend")
        check(uip.text("Name:", default="Ash").ask() == "Ash!", "text goes through the backend")
        check(uip.confirm("Sure?", default=True).ask() is False, "confirm goes through the backend")
    finally:
        uip.set_backend(None)
    check(isinstance(uip.get_backend(), uip.TerminalBackend), "set_backend(None) restores the terminal")

    # The terminal backend still calls questionary.select at call time (tests patch it).
    import questionary
    from unittest import mock
    with mock.patch.object(questionary, "select",
                           lambda *a, **k: type("Q", (), {"ask": lambda s: "x"})()):
        check(uip.select("Pick:", ["x", "y"]).ask() == "x", "terminal backend uses questionary.select")

    # Nothing in the game calls questionary directly any more.
    offenders = []
    for folder in ("core", "systems", "scenes", "ui"):
        for f in (ROOT / folder).glob("*.py"):
            if f.name == "prompt.py":
                continue
            text = f.read_text(encoding="utf-8")
            if "questionary." in text or "import questionary" in text:
                offenders.append(f.name)
    check(not offenders, f"every prompt goes through ui/prompt (direct questionary use: {offenders})")


# ── utils/session_log ─────────────────────────────────────────────────────────

def test_session_log() -> None:
    import logging
    import threading
    import zipfile
    from utils import session_log as sl

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        old = (sl.LOG_DIR, sl.SESSIONS_DIR, sl.EXPORTS_DIR, sl.SETTINGS_FILE)
        sl.LOG_DIR, sl.SESSIONS_DIR, sl.EXPORTS_DIR = tmp, tmp / "sessions", tmp / "exports"
        sl.SETTINGS_FILE = tmp / "settings.json"
        try:
            from rich.console import Console
            from ui.themes import GAME_THEME
            con = Console(theme=GAME_THEME, file=open(os.devnull, "w"), width=100)
            path = sl.start(console=con)
            check(path is not None and sl.is_enabled(), "the diagnostics log starts")
            con.print("[system_msg]A screen line[/system_msg]")
            from ui import prompt as uip

            class B:
                def select(self, m, c, d=None):
                    return c[0].value
            uip.set_backend(B())
            uip.select("Choose:", ["Enter the gate"]).ask()
            uip.set_backend(None)
            sl.event("scene", scene="village_start")
            logging.getLogger("core.test").warning("a warning")
            t = threading.Thread(target=lambda: 1 / 0, name="bg-test")
            t.start(); t.join()
            out = sl.export_bundle()
            sl.stop()
            text = path.read_text(encoding="utf-8")
            check("A screen line" in text, "screen output is copied as plain text")
            check(">> CHOICE Choose: -> Enter the gate" in text, "choices are logged")
            check("## EVENT scene scene='village_start'" in text, "events are logged")
            check("WARNING core.test" in text, "log records are copied")
            check("!! CRASH in thread bg-test" in text and "ZeroDivisionError" in text,
                  "crashes on background threads are caught")
            names = zipfile.ZipFile(out).namelist() if out else []
            check("summary.txt" in names and any(n.startswith("sessions/") for n in names),
                  "export zips the summary and session logs")
            check(not sl.is_enabled() and not con._render_hooks, "stop() unhooks everything")
            sl.set_enabled(False)
            check(sl.saved_enabled() is False, "the on/off choice is remembered")
            sl.set_enabled(True); sl.stop()
        finally:
            sl.LOG_DIR, sl.SESSIONS_DIR, sl.EXPORTS_DIR, sl.SETTINGS_FILE = old


# ── ui/tui_app ────────────────────────────────────────────────────────────────

async def _tui_flow() -> None:
    from textual.widgets import Label, OptionList, RichLog, Static
    from ui.tui_app import GameApp

    app = GameApp()

    async def wait(pilot, secs=90):
        for _ in range(secs * 10):
            await pilot.pause(0.1)
            if app._kind:
                return True
        return False

    def options():
        ol = app.query_one("#choices", OptionList)
        return [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]

    def story():
        return "\n".join(l.text for l in app.query_one("#story", RichLog).lines)

    async with app.run_test(size=(140, 45)) as pilot:
        check(await wait(pilot), "the engine reaches the main menu")
        check("New Game" in options(), f"main menu choices are clickable options ({options()})")
        await pilot.press("enter")                                  # New Game
        check(await wait(pilot) and app._kind == "text", "the name prompt becomes a text box")
        app.query_one("#entry").value = "Pilot"
        await pilot.press("enter")
        for _ in range(40):                                         # through creation
            if not await wait(pilot):
                break
            if app._kind == "text":
                app.query_one("#entry").value = "Pilot"
            if any("Check the Adventurers' Board" in o for o in options()):
                break
            await pilot.press("enter")
        check(any("Check the Adventurers' Board" in o for o in options()),
              "playing on with Enter reaches the village")
        check("Outer Verath" in story(), "scene text lands in the story log")
        with app.console.capture() as cap:
            app.console.print(getattr(app, "sidebar_renderable", ""))
        check("Pilot" in cap.get() and "HP" in cap.get(), "the sidebar shows the player's status")
        check({"k", "i", "j"} <= set(app._hotkeys), f"hotkeys become buttons ({sorted(app._hotkeys)})")
        locked = [o for o in options() if "(" in o and "Verath" in o]
        check(bool(locked), "locked choices are listed with their reason")
        await pilot.press("k")                                      # Skills hotkey
        check(await wait(pilot) and "Back" in " ".join(options()), "a hotkey letter opens its menu")
        await pilot.press("ctrl+q")


def test_tui() -> None:
    try:
        import textual  # noqa: F401
    except ImportError:
        print("  -  textual not installed: Textual app checks skipped (pip install textual)")
        return
    asyncio.run(_tui_flow())


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()
    print("\n  ui/prompt")
    test_prompt_layer()
    print("\n  utils/session_log")
    test_session_log()
    print("\n  ui/tui_app")
    test_tui()
    for f in list(ROOT.glob("saves/pilot*")) + list(ROOT.glob("saves/Pilot*")):
        f.unlink()
    print()
    if failures:
        print(f"  {len(failures)} UI check(s) failed.")
        sys.exit(1)
    print("  All UI checks passed.")
