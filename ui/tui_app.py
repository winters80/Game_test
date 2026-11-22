"""
SYSTEM BREAKER in a Textual window: runs in a terminal (`python main.py --tui`)
or in a web browser (`python main.py --web`, which uses textual-serve).

Layout::

    ┌ sidebar ──────────┬ story ─────────────────────────────────────┐
    │ status / stats    │ everything the game prints, scrollable    │
    │ equipment         │                                           │
    │ inventory         ├ prompt ───────────────────────────────────┤
    │                   │ the question + clickable choices / input  │
    │                   ├ hotkeys ──────────────────────────────────┤
    │                   │ [I] Inventory  [J] Journal  [?] Ask  ...  │
    └───────────────────┴───────────────────────────────────────────┘

How it works: the game engine is unchanged and runs on a worker thread.
``ui/prompt`` gets a ``TuiBackend`` so every select / text / confirm /
any-key becomes a widget here, and the engine thread blocks until the
player answers. Everything ``ui.renderer.console`` prints is caught by a
Rich render hook, rendered with the game's theme, and appended to the story
log instead of the real terminal.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
from contextlib import contextmanager
from typing import Any

from rich.console import Group
from rich.rule import Rule
from rich.segment import Segment
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ui import prompt as uip

logger = logging.getLogger(__name__)

_HOTKEY_RE = re.compile(r"^\[(?P<key>[A-Z?])\]\s")
_QUIT = object()                  # put on the answer queue when the app closes


def _to_text(console, renderable, width: int) -> Text:
    """Render with the game's console (so theme styles resolve) into a
    plain Text the Textual widgets can show at any width."""
    opts = console.options.update(width=max(20, width))
    lines = console.render_lines(renderable, opts, pad=False)
    out = Text()
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        for seg in Segment.simplify(line):
            if not seg.control:
                out.append(seg.text, seg.style)
    out.rstrip()
    return out


class _StoryHook:
    """Rich render hook: send what the game prints to the story log."""

    def __init__(self, app: "GameApp") -> None:
        self.app = app

    def process_renderables(self, renderables):
        # The app's console carries the game theme, so the log can render
        # these natively at its own width (and re-wrap on resize).
        self.app.post_story(Group(*renderables))
        return []                                            # nothing to the real terminal


class TuiBackend:
    """ui/prompt backend: questions become widgets; the engine thread waits."""

    def __init__(self, app: "GameApp") -> None:
        self.app = app

    def _ask(self, kind: str, message: str, payload: Any) -> Any:
        self.app.call_from_thread(self.app.show_prompt, kind, message, payload)
        answer = self.app.answers.get()
        if answer is _QUIT:
            raise SystemExit(0)
        return answer

    # -- prompt protocol --
    def select(self, message, choices, default=None):
        return self._ask("select", message, (choices, default))

    def text(self, message, default=""):
        return self._ask("text", message, default)

    def confirm(self, message, default=True):
        return self._ask("select", message, ([uip.Choice("Yes", True), uip.Choice("No", False)],
                                             True if default else False))

    def any_key(self, message):
        self._ask("select", message.strip() or "Continue", ([uip.Choice("Continue ▸", True)], None))

    # -- renderer hooks --
    hides_title = True

    def any_key_hook(self):
        self.any_key("")

    def clear(self):
        self.app.post_story(Rule(style="grey35"))

    def status(self, player, item_registry):
        from ui.panels import (build_compact_inventory_panel, build_equipment_panel,
                               build_stats_panel, build_status_panel)
        from ui.renderer import console
        panels = [build_status_panel(player), build_stats_panel(player)]
        if item_registry is not None:
            panels += [build_equipment_panel(player, item_registry),
                       build_compact_inventory_panel(player, item_registry)]
        self.app.call_from_thread(self.app.set_sidebar, Group(*panels))

    def set_busy_text(self, message):
        self.app.call_from_thread(self.app.set_busy, message)

    @contextmanager
    def spinner(self, message):
        clean = Text.from_markup(message).plain if "[" in message else message
        self.app.call_from_thread(self.app.set_busy, clean)
        try:
            yield self
        finally:
            self.app.call_from_thread(self.app.set_busy, "")


class GameApp(App):
    """The game, in panels."""

    TITLE = "SYSTEM BREAKER"
    SUB_TITLE = "The Universe Is Watching"
    CSS = """
    Screen { layout: horizontal; background: #0b0d12; }
    #sidebar { width: 42; border-right: tall #2a3142; padding: 0 1; }
    #main { width: 1fr; }
    #story { height: 1fr; border: round #2a3142; padding: 0 1; scrollbar-size: 1 1; }
    #busy { height: auto; color: #c58cff; text-style: bold; padding: 0 2; display: none; }
    #busy.on { display: block; }
    #prompt { height: auto; max-height: 50%; border: round #3d6fb6; padding: 0 1; }
    #question { color: #7fb2ff; text-style: bold; padding: 0 1; }
    #choices { height: auto; max-height: 20; border: none; background: transparent; }
    #choices > .option-list--option-disabled { color: #5c6370; }
    #entry { display: none; }
    #entry.on { display: block; }
    #hotkeys { layout: grid; grid-size: 5; grid-gutter: 0 1; height: auto; padding: 0 1; }
    #hotkeys Button { width: 100%; min-width: 0; height: 1; border: none; padding: 0 1;
                      background: #1b2333; color: #a9c1e8; }
    #hotkeys Button:hover { background: #2a3a57; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit_game", "Quit"),
        Binding("ctrl+l", "focus_story", "Scroll story"),
        Binding("escape", "focus_choices", "Choices", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.answers: "queue.Queue[Any]" = queue.Queue()
        self._kind = ""
        self._values: list[Any] = []
        self._hotkeys: dict[str, Any] = {}
        self._engine_thread: threading.Thread | None = None

    # ── layout ──
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="sidebar"):
            yield Static("", id="panels")
        with Vertical(id="main"):
            yield RichLog(id="story", wrap=True, markup=False, highlight=False, auto_scroll=True)
            yield Label("", id="busy")
            with Vertical(id="prompt"):
                yield Label("", id="question")
                yield OptionList(id="choices")
                yield Input(id="entry")
            yield Horizontal(id="hotkeys")
        yield Footer()

    def on_mount(self) -> None:
        from ui.renderer import console
        from ui.themes import GAME_THEME
        self.console.push_theme(GAME_THEME)          # [system_msg], [gold] … resolve here too
        self._hook = _StoryHook(self)
        console.push_render_hook(self._hook)
        uip.set_backend(TuiBackend(self))
        _quiet_console_logging()
        self._engine_thread = threading.Thread(target=self._run_engine, name="engine", daemon=True)
        self._engine_thread.start()

    def _run_engine(self) -> None:
        try:
            from core.game_engine import GameEngine
            GameEngine().run()
        except SystemExit:
            pass
        except Exception:
            logger.exception("Engine crashed in the TUI")
            import traceback
            self.post_story(Text("The game hit an error:\n" + traceback.format_exc(), style="red"))
            self.answers.get()                       # let the player read it
        finally:
            try:
                self.call_from_thread(self.exit)
            except Exception:
                pass

    # ── called from the engine thread ──
    def story_width(self) -> int:
        try:
            return max(40, self.query_one("#story").scrollable_content_region.width - 1)
        except Exception:
            return 100

    def sidebar_width(self) -> int:
        try:
            return max(30, self.query_one("#sidebar").scrollable_content_region.width - 1)
        except Exception:
            return 40

    def post_story(self, renderable) -> None:
        if threading.current_thread() is threading.main_thread():
            self.query_one("#story", RichLog).write(renderable)
        else:
            self.call_from_thread(lambda: self.query_one("#story", RichLog).write(renderable))

    # ── UI-thread side ──
    def set_sidebar(self, renderable) -> None:
        self.sidebar_renderable = renderable
        self.query_one("#panels", Static).update(renderable)

    def set_busy(self, message: str) -> None:
        busy = self.query_one("#busy", Label)
        busy.update(f"⟳ {message}" if message else "")
        busy.set_class(bool(message), "on")

    def show_prompt(self, kind: str, message: str, payload: Any) -> None:
        self._kind = kind
        question = self.query_one("#question", Label)
        choices = self.query_one("#choices", OptionList)
        entry = self.query_one("#entry", Input)
        bar = self.query_one("#hotkeys", Horizontal)
        question.update(Text.from_markup(message) if "[" in message and "]" in message else message)
        choices.clear_options()
        bar.remove_children()
        self._values, self._hotkeys = [], {}

        if kind == "text":
            choices.display = False
            entry.value = payload or ""
            entry.placeholder = Text.from_markup(message).plain if "[" in message else message
            entry.set_class(True, "on")
            entry.focus()
            return

        entry.set_class(False, "on")
        choices.display = True
        items, default = payload
        options, buttons, n = [], [], 0
        for c in items:
            hk = _HOTKEY_RE.match(c.title) if isinstance(c.title, str) else None
            if hk and kind == "select" and c.disabled is None:
                key = hk.group("key")
                self._hotkeys[key.lower()] = c.value
                short = _short_label(_HOTKEY_RE.sub("", c.title))
                buttons.append(Button(Text(f"[{key}] {short}"), id=f"hk-{len(self._hotkeys)}",
                                      tooltip=c.title, compact=True))
                buttons[-1].game_value = c.value
                continue
            n += 1
            label = Text.from_markup(c.title) if "[" in c.title else Text(c.title)
            if c.disabled:
                reason = c.disabled if isinstance(c.disabled, str) else "locked"
                label.append(f"  ({reason})", style="dim")
            options.append(Option(label, id=f"o{len(self._values)}", disabled=bool(c.disabled)))
            self._values.append(c.value)
        choices.add_options(options)
        if buttons:
            bar.mount_all(buttons)
        if default is not None and default in self._values:
            choices.highlighted = self._values.index(default)
        else:
            first = next((i for i, o in enumerate(options) if not o.disabled), None)
            if first is not None:
                choices.highlighted = first
        choices.focus()

    def _answer(self, value: Any) -> None:
        if not self._kind:
            return
        self._kind = ""
        self.query_one("#choices", OptionList).clear_options()
        self.query_one("#hotkeys", Horizontal).remove_children()
        self.query_one("#entry", Input).set_class(False, "on")
        self.query_one("#question", Label).update("")
        self.answers.put(value)

    @on(OptionList.OptionSelected, "#choices")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        idx = int(event.option.id[1:])
        self._answer(self._values[idx])

    @on(Input.Submitted, "#entry")
    def _typed(self, event: Input.Submitted) -> None:
        self._answer(event.value)

    @on(Button.Pressed, "#hotkeys Button")
    def _hotkey(self, event: Button.Pressed) -> None:
        self._answer(getattr(event.button, "game_value", None))

    def on_key(self, event) -> None:
        if self._kind != "select" or isinstance(self.focused, Input):
            return
        key = event.character or ""
        if key.isdigit() and key != "0":                  # 1-9 pick the choice with that number
            choices = self.query_one("#choices", OptionList)
            for idx in range(choices.option_count):
                opt = choices.get_option_at_index(idx)
                if str(opt.prompt).startswith(f"{key}.") and not opt.disabled:
                    event.stop()
                    self._answer(self._values[idx])
                    return
        elif (key.isalpha() or key == "?") and key.lower() in self._hotkeys:
            event.stop()
            self._answer(self._hotkeys[key.lower()])

    def action_focus_story(self) -> None:
        self.query_one("#story").focus()

    def action_focus_choices(self) -> None:
        self.query_one("#choices").focus()

    def action_quit_game(self) -> None:
        self.answers.put(_QUIT)
        self.exit()

    def on_unmount(self) -> None:
        from ui.renderer import console
        try:
            console._render_hooks.remove(self._hook)
        except Exception:
            pass
        uip.set_backend(None)
        self.answers.put(_QUIT)


_FILLER = {"&", "and", "a", "an", "the", "to", "about", "of", "your", "this"}


def _short_label(title: str, limit: int = 16) -> str:
    """'Items & Equipment' → 'Items', 'Quit to menu' → 'Quit', 'Who's around' stays."""
    words = Text.from_markup(title).plain.split() if "[" in title else title.split()
    out: list[str] = []
    for w in words:
        if len(" ".join(out + [w])) > limit:
            break
        out.append(w)
    while out and out[-1].lower() in _FILLER:
        out.pop()
    return " ".join(out) or (words[0] if words else title)


def _quiet_console_logging() -> None:
    """Terminal log output would scribble over the app; the log files (and
    the diagnostics log) still get everything."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if type(h) is logging.StreamHandler:
            root.removeHandler(h)


def run_tui() -> None:
    GameApp().run()


def run_web(host: str = "localhost", port: int = 8000) -> None:
    """Serve the app to a browser (needs ``pip install textual-serve``)."""
    import sys
    from pathlib import Path
    try:
        from textual_serve.server import Server
    except ImportError:
        print("Browser mode needs textual-serve:  pip install textual-serve")
        return
    main = Path(__file__).resolve().parent.parent / "main.py"
    print(f"Open http://{host}:{port} in your browser (Ctrl+C here to stop).")
    Server(f'"{sys.executable}" "{main}" --tui', host=host, port=port,
           title="SYSTEM BREAKER").serve()
