"""
Diagnostics log: one transcript per play session, to help diagnose problems.

When it is on (Admin panel → « Diagnostics log »; the choice is remembered in
``logs/diagnostics_settings.json``), ``logs/sessions/session_<time>.log`` gets:

- everything the game printed (plain text, as the player saw it)
- every choice the player made, and each prompt answer      (``>> CHOICE``)
- scene / node moves, combats and other milestones          (``## EVENT``)
- every log record at INFO or above from *any* thread, including the
  background AI workers                                     (``.. LOG``)
- uncaught exceptions with their tracebacks, on any thread  (``!! CRASH``)

« Export diagnostics » zips the current session log, the rotating logs
(game / error / ai / player_errors), and a summary of the setup (Python,
models, feature flags, the player's state) into ``logs/exports/``.

Everything here is best-effort: a logging failure never breaks the game.
"""
from __future__ import annotations

import json
import logging
import platform
import sys
import threading
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).parent.parent / "logs"
SESSIONS_DIR = LOG_DIR / "sessions"
EXPORTS_DIR = LOG_DIR / "exports"
SETTINGS_FILE = LOG_DIR / "diagnostics_settings.json"
KEEP_SESSIONS = 20                    # older session logs are deleted

_lock = threading.RLock()
_file = None                          # open text file while recording
_path: Path | None = None
_handler: logging.Handler | None = None
_console = None                       # rich Console writing to _file
_hook = None
_prev_excepthook = None
_prev_thread_hook = None


# ── Settings ──────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def saved_enabled() -> bool:
    """The remembered on/off choice (default on)."""
    return bool(_load_settings().get("enabled", True))


def _save_enabled(on: bool) -> None:
    try:
        LOG_DIR.mkdir(exist_ok=True)
        data = _load_settings()
        data["enabled"] = on
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_enabled() -> bool:
    return _file is not None


def current_path() -> Path | None:
    return _path


# ── Writing ───────────────────────────────────────────────────────────────────

def _write(prefix: str, text: str) -> None:
    if _file is None:
        return
    try:
        with _lock:
            stamp = datetime.now().strftime("%H:%M:%S")
            lines = str(text).rstrip("\n").splitlines() or [""]
            _file.write(f"{stamp} {prefix} {lines[0]}\n")
            for extra in lines[1:]:
                _file.write(f"         {' ' * len(prefix)} {extra}\n")
            _file.flush()
    except Exception:
        pass


def event(kind: str, **data: Any) -> None:
    """A game milestone: ``event("scene", scene="village_start", node="root")``."""
    if _file is None:
        return
    detail = " ".join(f"{k}={v!r}" for k, v in data.items())
    _write("## EVENT", f"{kind} {detail}".rstrip())


def choice(prompt: str, answer: Any) -> None:
    """What the player picked (or typed) at a prompt."""
    if _file is None:
        return
    _write(">> CHOICE", f"{_plain(prompt)} -> {_plain(answer)}")


def _plain(value: Any) -> str:
    text = value
    if not isinstance(value, (str, int, float, bool, type(None))):
        for attr in ("label", "title"):
            got = getattr(value, attr, None)
            if isinstance(got, str):
                text = got
                break
    try:
        from rich.text import Text
        return Text.from_markup(str(text)).plain
    except Exception:
        return str(text)


class _SessionHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("httpx", "httpcore", "urllib3")):
            return
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + "".join(traceback.format_exception(*record.exc_info))
            thread = "" if record.threadName == "MainThread" else f" [{record.threadName}]"
            _write(".. LOG", f"{record.levelname} {record.name}{thread}: {msg}")
        except Exception:
            pass


class _ScreenTee:
    """Rich render hook: a plain-text copy of everything printed."""
    def process_renderables(self, renderables):
        if _console is not None:
            try:
                with _lock:
                    for r in renderables:
                        _console.print(r)
                    _file.flush()
            except Exception:
                pass
        return renderables


def _excepthook(exc_type, exc, tb):
    _write("!! CRASH", "".join(traceback.format_exception(exc_type, exc, tb)))
    if _prev_excepthook:
        _prev_excepthook(exc_type, exc, tb)


def _thread_excepthook(args):
    _write("!! CRASH", f"in thread {getattr(args.thread, 'name', '?')}:\n"
           + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    if _prev_thread_hook:
        _prev_thread_hook(args)


# ── On / off ─────────────────────────────────────────────────────────────────

def start(console=None) -> Path | None:
    """Begin recording this session (no-op if already recording)."""
    global _file, _path, _handler, _console, _hook, _prev_excepthook, _prev_thread_hook
    with _lock:
        if _file is not None:
            return _path
        try:
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            _prune()
            _path = SESSIONS_DIR / f"session_{datetime.now():%Y%m%d_%H%M%S}.log"
            _file = open(_path, "a", encoding="utf-8")
        except Exception:
            _file = _path = None
            return None
        _file.write(f"=== SYSTEM BREAKER diagnostics log · {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        _file.write(_environment_text() + "\n\n")
        _handler = _SessionHandler(level=logging.INFO)
        logging.getLogger().addHandler(_handler)
        if console is None:
            try:
                from ui.renderer import console as console   # noqa: PLW0127
            except Exception:
                console = None
        if console is not None:
            try:
                from rich.console import Console
                _console = Console(file=_file, width=100, color_system=None,
                                   force_terminal=False, emoji=False, highlight=False)
                _copy_theme(console, _console)
                _hook = _ScreenTee()
                console.push_render_hook(_hook)
                _console._sb_owner = console            # remembered for stop()
            except Exception:
                _console = _hook = None
        _prev_excepthook, sys.excepthook = sys.excepthook, _excepthook
        _prev_thread_hook, threading.excepthook = threading.excepthook, _thread_excepthook
        return _path


def _copy_theme(src, dst) -> None:
    """Give the file console the game's style names, so markup like
    [system_msg] renders instead of raising a missing-style error."""
    try:
        from ui.themes import GAME_THEME
        dst.push_theme(GAME_THEME)
    except Exception:
        try:
            dst._theme_stack = src._theme_stack
        except Exception:
            pass


def stop() -> None:
    global _file, _handler, _console, _hook
    with _lock:
        if _file is None:
            return
        if _handler is not None:
            logging.getLogger().removeHandler(_handler)
        if _hook is not None and _console is not None:
            try:
                owner = getattr(_console, "_sb_owner", None)
                if owner is not None and owner._render_hooks and owner._render_hooks[-1] is _hook:
                    owner.pop_render_hook()
                elif owner is not None and _hook in owner._render_hooks:
                    owner._render_hooks.remove(_hook)
            except Exception:
                pass
        if sys.excepthook is _excepthook:
            sys.excepthook = _prev_excepthook or sys.__excepthook__
        if threading.excepthook is _thread_excepthook:
            threading.excepthook = _prev_thread_hook or threading.__excepthook__
        try:
            _file.write(f"=== log stopped {datetime.now():%H:%M:%S} ===\n")
            _file.close()
        except Exception:
            pass
        _file = _handler = _console = _hook = None


def set_enabled(on: bool) -> Path | None:
    """Switch recording on/off now and remember the choice for next time."""
    _save_enabled(on)
    if on:
        return start()
    stop()
    return None


def start_if_enabled() -> Path | None:
    return start() if saved_enabled() else None


def _prune() -> None:
    try:
        logs = sorted(SESSIONS_DIR.glob("session_*.log"))
        for old in logs[:-(KEEP_SESSIONS - 1)] if len(logs) >= KEEP_SESSIONS else []:
            old.unlink()
    except Exception:
        pass


# ── Export ────────────────────────────────────────────────────────────────────

def _environment_text(engine=None) -> str:
    lines = [f"python   : {sys.version.split()[0]} on {platform.platform()}"]
    try:
        import config
        lines.append(f"models   : primary={config.OLLAMA_MODEL} fast={config.OLLAMA_FAST_MODEL or '(primary)'}"
                     f" url={config.OLLAMA_BASE_URL}")
        off = [k for k, v in config.FEATURES.items() if not v]
        lines.append(f"features : all on" + (f" except {', '.join(off)}" if off else ""))
        lines.append(f"save ver : {config.SAVE_VERSION}")
    except Exception:
        pass
    if engine is not None:
        try:
            lines.append(f"AI       : {'online' if engine._ai_online() else 'offline'}"
                         f" {getattr(engine, 'ai_offline_reason', '') or ''}".rstrip())
        except Exception:
            pass
        state = getattr(engine, "state", None)
        p = getattr(state, "player", None)
        if p is not None:
            try:
                lines += [
                    f"player   : {p.name} Lv {p.level} {p.active_class or p.base_class or 'unclassed'}"
                    f" HP {p.current_hp}/{p.max_hp} MP {p.current_mp}/{p.max_mp} gold {p.gold}c",
                    f"where    : scene={state.current_scene_id} node={state.current_node_id}"
                    f" turn={getattr(state, 'turn_number', p.turn_count)}",
                    f"equipped : {getattr(p, 'equipment', {})}",
                    f"items    : {dict(list(p.inventory.items())[:40]) if isinstance(p.inventory, dict) else p.inventory}",
                    f"flags    : {sorted(p.flags)}",
                    f"quests   : {p.active_quest_ids}",
                ]
            except Exception:
                lines.append("player   : <unreadable>")
    return "\n".join(lines)


def export_bundle(engine=None) -> Path | None:
    """Zip the logs and a setup summary for a bug report. Returns the zip path."""
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = EXPORTS_DIR / f"diagnostics_{datetime.now():%Y%m%d_%H%M%S}.zip"
        if _file is not None:
            with _lock:
                _file.flush()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("summary.txt", f"Exported {time.ctime()}\n{_environment_text(engine)}\n")
            for name in ("game.log", "error.log", "ai.log", "player_errors.log"):
                f = LOG_DIR / name
                if f.exists():
                    z.write(f, name)
            sessions = sorted(SESSIONS_DIR.glob("session_*.log")) if SESSIONS_DIR.exists() else []
            for f in sessions[-3:]:                 # this session and the two before it
                z.write(f, f"sessions/{f.name}")
        return out
    except Exception:
        logging.getLogger(__name__).exception("Diagnostics export failed")
        return None
