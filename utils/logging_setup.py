"""
Logging setup for SYSTEM BREAKER.

Four rotating log files under logs/:
  game.log          — INFO+  (general events: scene transitions, quests, deaths, saves)
  error.log         — ERROR+ (exceptions, validation failures, save errors)
  ai.log            — DEBUG+ (all Ollama calls: prompts truncated, responses, timing, failures)
  player_errors.log — ERROR+ structured gameplay errors with full player/scene context

Console handler: WARNING+ only (don't flood the terminal).

Call setup_logging() once at startup in main.py.
Use log_player_error() anywhere in the codebase to emit a structured player error entry.
"""
from __future__ import annotations

import logging
import logging.handlers
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entities.player import Player


def setup_logging(log_dir: Path | None = None) -> None:
    """
    Configure root logger and named loggers.
    Safe to call multiple times — handlers are only added if none exist yet.
    """
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    root.setLevel(logging.DEBUG)

    fmt_full = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fmt_brief = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── game.log — INFO+ ──────────────────────────────────────────────────────
    game_handler = logging.handlers.RotatingFileHandler(
        log_dir / "game.log",
        maxBytes=2 * 1024 * 1024,   # 2 MB
        backupCount=3,
        encoding="utf-8",
    )
    game_handler.setLevel(logging.INFO)
    game_handler.setFormatter(fmt_full)
    root.addHandler(game_handler)

    # ── error.log — ERROR+ ────────────────────────────────────────────────────
    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "error.log",
        maxBytes=1 * 1024 * 1024,   # 1 MB
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt_full)
    root.addHandler(error_handler)

    # ── ai.log — DEBUG+ (ai.* loggers only) ──────────────────────────────────
    ai_handler = logging.handlers.RotatingFileHandler(
        log_dir / "ai.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=2,
        encoding="utf-8",
    )
    ai_handler.setLevel(logging.DEBUG)
    ai_handler.setFormatter(fmt_full)
    ai_handler.addFilter(_NameFilter("ai"))   # only ai.* loggers
    root.addHandler(ai_handler)

    # ── player_errors.log — ERROR+ (structured gameplay errors) ──────────────
    player_error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "player_errors.log",
        maxBytes=2 * 1024 * 1024,   # 2 MB
        backupCount=5,
        encoding="utf-8",
    )
    player_error_handler.setLevel(logging.ERROR)
    player_error_handler.setFormatter(fmt_full)
    player_error_handler.addFilter(_NameFilter("player_errors"))
    root.addHandler(player_error_handler)

    # ── Console — WARNING+ ────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt_brief)
    # Suppress noisy third-party warnings from the terminal
    console_handler.addFilter(_SuppressFilter(["httpx", "httpcore", "urllib3", "ollama"]))
    # Background AI work (world growth, quests, the world director) runs on
    # worker threads while the player is at a prompt; a warning from there
    # would print over the menu. Those go to the log files only.
    console_handler.addFilter(_MainThreadOnly())
    root.addHandler(console_handler)

    logging.getLogger("ai").setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.info("Logging initialised. Log dir: %s", log_dir)


class _MainThreadOnly(logging.Filter):
    """Pass only records logged on the main (UI) thread."""
    def filter(self, record: logging.LogRecord) -> bool:
        return record.threadName == "MainThread"


class _NameFilter(logging.Filter):
    """Only pass records whose logger name starts with the given prefix."""
    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self.prefix)


class _SuppressFilter(logging.Filter):
    """Drop records from any of the named loggers."""
    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.names = names

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(record.name.startswith(n) for n in self.names)


# ── Structured player error logger ────────────────────────────────────────────

_player_error_log = logging.getLogger("player_errors")


def log_player_error(
    event: str,
    exc: BaseException | None = None,
    player: "Player | None" = None,
    scene_id: str = "",
    node_id: str = "",
    option_id: str = "",
    turn: int = 0,
    extra: dict | None = None,
) -> None:
    """
    Emit a structured error entry to player_errors.log.

    Each entry contains:
      - event     : short label for the error type (e.g. "trigger_crash", "combat_crash")
      - player    : name, level, class, scene, node, turn, alignment, flags count
      - option_id : which option triggered the crash (if applicable)
      - exception : type + message + full traceback
      - extra     : any additional key/value context the caller wants to include

    Usage:
        from utils.logging_setup import log_player_error
        try:
            ...
        except Exception as e:
            log_player_error("trigger_crash", exc=e, player=state.player,
                             scene_id=state.current_scene_id,
                             node_id=state.current_node_id,
                             option_id=option.option_id)
            raise
    """
    parts: list[str] = [f"EVENT={event}"]

    if player is not None:
        try:
            base = getattr(player, "base_class_id", None) or "none"
            combo = getattr(player, "combo_class_id", None) or "none"
            parts += [
                f"player={player.name!r}",
                f"level={player.level}",
                f"class={base}/{combo}",
                f"species={getattr(player, 'species_id', '?')}",
                f"alignment={getattr(player, 'alignment', 0.0):.1f}",
                f"flags={len(player.flags)}",
                f"hp={getattr(player, 'current_hp', '?')}/{getattr(player, 'max_hp', '?')}",
            ]
        except Exception:
            parts.append("player=<unreadable>")

    if scene_id:
        parts.append(f"scene={scene_id}")
    if node_id:
        parts.append(f"node={node_id}")
    if option_id:
        parts.append(f"option={option_id}")
    if turn:
        parts.append(f"turn={turn}")

    if extra:
        for k, v in extra.items():
            parts.append(f"{k}={v!r}")

    if exc is not None:
        tb = traceback.format_exc()
        exc_line = f"{type(exc).__name__}: {exc}"
        msg = " | ".join(parts) + f"\n  EXCEPTION: {exc_line}\n  TRACEBACK:\n{tb}"
    else:
        msg = " | ".join(parts)

    _player_error_log.error(msg)
