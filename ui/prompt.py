"""
Every question the game asks the player goes through here.

    from ui import prompt as uip
    pick = uip.select("Choose:", choices=["Fight", uip.Choice("Flee", value="flee")]).ask()
    name = uip.text("Your name:", default="Ash").ask()
    ok   = uip.confirm("Sure?", default=True).ask()

The call shape matches questionary (build the question, then ``.ask()``), so
the terminal behaves exactly as before. A different front end, like the
Textual app in ``ui/tui_app.py``, swaps in its own backend with
``set_backend`` and gets every select / text / confirm / any-key as a call
it can answer with its own widgets. Each answer is also written to the
diagnostics log (``utils/session_log``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Choice:
    """One selectable entry. ``disabled`` (a reason string, or True) shows
    the entry greyed out and unselectable."""
    title: str
    value: Any = None
    disabled: Any = None

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = self.title


def _norm(choices) -> list[Choice]:
    out = []
    for c in choices:
        if isinstance(c, Choice):
            out.append(c)
        elif isinstance(c, str):
            out.append(Choice(c))
        else:                                    # a questionary.Choice or similar
            title = getattr(c, "title", str(c))
            if isinstance(title, list):          # questionary formatted-text tuples
                title = "".join(t[1] for t in title)
            out.append(Choice(str(title), getattr(c, "value", title), getattr(c, "disabled", None)))
    return out


class Backend(Protocol):
    def select(self, message: str, choices: list[Choice], default: Any = None) -> Any: ...
    def text(self, message: str, default: str = "") -> str | None: ...
    def confirm(self, message: str, default: bool = True) -> bool | None: ...
    def any_key(self, message: str) -> None: ...


class TerminalBackend:
    """questionary in the terminal (looked up at call time, so tests can
    patch ``questionary.select`` as before)."""

    def select(self, message, choices, default=None):
        import questionary
        qs = [c.title if (c.disabled is None and c.value == c.title)
              else questionary.Choice(c.title, value=c.value, disabled=c.disabled) for c in choices]
        kw = {"default": default} if default is not None else {}
        return questionary.select(message, choices=qs, **kw).ask()

    def text(self, message, default="", instruction=None):
        import questionary
        kw = {"instruction": instruction} if instruction else {}
        return questionary.text(message, default=default, **kw).ask()

    def confirm(self, message, default=True):
        import questionary
        return questionary.confirm(message, default=default).ask()

    def any_key(self, message):
        from ui.renderer import console
        console.input(f"[dim_text]{message}[/dim_text]")


_backend: Backend = TerminalBackend()


def set_backend(backend: Backend | None) -> None:
    global _backend
    _backend = backend or TerminalBackend()


def get_backend() -> Backend:
    return _backend


def _log(message: str, answer: Any) -> None:
    try:
        from utils import session_log
        session_log.choice(message, answer)
    except Exception:
        pass


class _Question:
    def __init__(self, fn, message: str, clicks: bool = False) -> None:
        self._fn, self._message, self._clicks = fn, message, clicks

    def ask(self) -> Any:
        try:
            answer = self._fn()
        except KeyboardInterrupt:
            answer = None
        _log(self._message, answer)
        if self._clicks:                             # menu confirm / cancel sound
            try:
                from ui import media
                media.ui_answer(answer)
            except Exception:
                pass
        return answer

    unsafe_ask = ask


def select(message: str, choices, default: Any = None, **_ignored) -> _Question:
    items = _norm(choices)
    return _Question(lambda: _backend.select(message, items, default), message, clicks=True)


def text(message: str, default: str = "", instruction: str | None = None, **_ignored) -> _Question:
    def run():
        try:
            return _backend.text(message, default, instruction=instruction) if instruction \
                else _backend.text(message, default)
        except TypeError:                 # a backend without instruction support
            return _backend.text(message, default)
    return _Question(run, message)


def confirm(message: str, default: bool = True, **_ignored) -> _Question:
    return _Question(lambda: _backend.confirm(message, default), message)


def any_key(message: str = "  Press Enter to continue...") -> None:
    _backend.any_key(message)
