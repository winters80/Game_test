"""
Logging setup for SYSTEM BREAKER.

Three rotating log files under logs/:
  game.log   — INFO+  (general events: scene transitions, quests, deaths, saves)
  error.log  — ERROR+ (exceptions, validation failures, save errors)
  ai.log     — DEBUG+ (all Ollama calls: prompts truncated, responses, timing, failures)

Console handler: WARNING+ only (don't flood the terminal).

Call setup_logging() once at startup in main.py.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


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

    # ── Console — WARNING+ ────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt_brief)
    # Suppress noisy third-party warnings from the terminal
    console_handler.addFilter(_SuppressFilter(["httpx", "httpcore", "urllib3", "ollama"]))
    root.addHandler(console_handler)

    logging.getLogger("ai").setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.info("Logging initialised. Log dir: %s", log_dir)


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
