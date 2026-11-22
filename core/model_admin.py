"""
Admin → « AI models »: see which model plays each role, check the catalog for
better ones, benchmark, download and switch, all without editing code.
A switch re-runs ``bootstrap.setup_ai`` so it takes effect immediately.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ui import prompt as uip
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


def _installed() -> set[str]:
    from ai.model_registry import installed_models
    return installed_models()


def _show_roles(engine, catalog, installed) -> None:
    from ai.model_registry import ROLES, benchmarks, model_info, resolve
    from ai.ollama_client import model_in
    measured = benchmarks()
    renderer.console.print("\n  [system_msg][ AI MODELS ][/system_msg]\n")
    for role in ROLES:
        name, src = resolve(role)
        info = model_info(catalog, name)
        have = "[success]installed[/success]" if model_in(name, installed) else "[system_warning]not downloaded[/system_warning]"
        bench = measured.get(name, {}).get("score")
        extra = f" · benchmark {bench}" if bench is not None else (
            f" · catalog {info['quality']}" if info.get("quality") else "")
        renderer.console.print(f"  {role:<9}[gold]{name}[/gold]  [dim_text]({src}){extra}[/dim_text]  {have}")
    running = getattr(engine, "ai_models", {}) or {}
    if running and running.get("primary") != resolve("primary")[0]:
        renderer.console.print(f"  [dim_text]Running now: {running.get('primary')} (switch applies on rewire)[/dim_text]")
    renderer.console.print()


def models_menu(engine: "GameEngine") -> None:
    from ai import model_registry as mr

    catalog, source = mr.load_catalog(remote=False)
    while True:
        installed = _installed()
        renderer.clear()
        renderer.print_title()
        _show_roles(engine, catalog, installed)
        ram = mr.system_ram_gb()
        renderer.console.print(f"  [dim_text]Memory: {ram:.0f} GB · catalog {catalog.get('updated', '?')} "
                               f"({source}) · Ollama: {'online' if installed or engine._ai_online() else 'offline'}"
                               f"[/dim_text]\n")
        action = uip.select("AI models:", choices=[
            "Check for better models",
            "Choose a model for a role",
            "Benchmark a model",
            "Reset roles to the defaults",
            "← Back",
        ]).ask()
        if action in (None, "← Back"):
            return
        if action.startswith("Check"):
            catalog = _check(engine, catalog, installed, ram)
        elif action.startswith("Choose"):
            _choose(engine, catalog, installed)
        elif action.startswith("Benchmark"):
            names = sorted(installed)
            if not names:
                renderer.print_error("No models installed (or Ollama is offline).")
                renderer.prompt_any_key()
                continue
            pick = uip.select("Benchmark which model?", choices=names + ["← Cancel"]).ask()
            if pick and pick != "← Cancel":
                _bench(pick)
                renderer.prompt_any_key()
        else:
            for role in mr.ROLES:
                mr.set_role(role, None)
            renderer.print_success("Roles reset to the config.py defaults.")
            _rewire(engine)
            renderer.prompt_any_key()


def _check(engine, catalog, installed, ram):
    from ai import model_registry as mr
    with renderer.show_ai_thinking_spinner("CHECKING THE MODEL CATALOG..."):
        catalog, source = mr.load_catalog(remote=True)
    renderer.console.print(f"\n  [dim_text]Catalog: {catalog.get('updated', '?')} ({source}).[/dim_text]")
    tips = mr.review(catalog, mr.active_models(), installed, ram)
    if not tips:
        renderer.print_success("You're on the recommended models for this machine.")
        renderer.prompt_any_key()
        return catalog
    labels = []
    for t in tips:
        if t.role == "?":
            labels.append(f"Benchmark {t.suggested} ({t.reason})")
        elif t.current == t.suggested:
            labels.append(f"Download {t.suggested} for {t.role} ({t.reason})")
        else:
            labels.append(f"{t.role}: {t.current} → {t.suggested}"
                          f"{'' if t.installed else ' (download)'} — {t.reason}")
    pick = uip.select("Suggestions:", choices=labels + ["← Back"]).ask()
    if pick in (None, "← Back"):
        return catalog
    tip = tips[labels.index(pick)]
    if tip.role == "?":
        _bench(tip.suggested)
        renderer.prompt_any_key()
        return catalog
    if not tip.installed and not _pull(tip.suggested):
        renderer.prompt_any_key()
        return catalog
    if uip.confirm(f"Benchmark {tip.suggested} before switching? (about a minute)", default=True).ask():
        res = _bench(tip.suggested)
        if res.score is not None and res.score < 40:
            if not uip.confirm(f"It scored {res.score}. Switch anyway?", default=False).ask():
                return catalog
    mr.set_role(tip.role, tip.suggested)
    renderer.print_success(f"{tip.role} is now {tip.suggested}.")
    _rewire(engine)
    renderer.prompt_any_key()
    return catalog


def _choose(engine, catalog, installed) -> None:
    from ai import model_registry as mr
    role = uip.select("Which role?", choices=[
        uip.Choice("primary — quests, classes, Gates (best quality)", "primary"),
        uip.Choice("fast — [?] questions, bot chat (quick)", "fast"),
        uip.Choice("fallback — if the primary won't load", "fallback"),
        uip.Choice("← Cancel", None)]).ask()
    if not role:
        return
    known = [m["name"] for m in catalog.get("models", []) if role in m.get("roles", [])]
    names = sorted(set(installed)) + [n for n in known if n not in installed]
    choices = [uip.Choice(n + ("" if n in installed else "  (download)"), n) for n in names]
    choices += [uip.Choice("Type a model name…", "__type__"), uip.Choice("← Cancel", None)]
    pick = uip.select(f"Model for {role}:", choices=choices).ask()
    if pick == "__type__":
        pick = (uip.text("Ollama model name (e.g. qwen3.5:9b):").ask() or "").strip()
    if not pick:
        return
    from ai.ollama_client import model_in
    if not model_in(pick, installed) and not _pull(pick):
        renderer.prompt_any_key()
        return
    mr.set_role(role, pick)
    renderer.print_success(f"{role} is now {pick}.")
    _rewire(engine)
    renderer.prompt_any_key()


def _pull(model: str) -> bool:
    from ai.model_registry import pull_model
    if not uip.confirm(f"Download {model} now? It can be several GB.", default=True).ask():
        return False
    with renderer.DownloadProgress(f"Downloading {model}") as bar:
        ok = pull_model(model, on_progress=bar.update)
    (renderer.print_success if ok else renderer.print_error)(
        f"Downloaded {model}." if ok else f"Couldn't download {model}. Is the name right? Try: ollama pull {model}")
    return ok


def _bench(model: str):
    from ai import model_registry as mr
    with renderer.show_ai_thinking_spinner(f"BENCHMARKING {model}..."):
        res = mr.benchmark(model)
    mr.record_benchmark(res)
    renderer.console.print(
        f"\n  [gold]{model}[/gold]: score [success]{res.score}[/success]/100 · "
        f"JSON {res.json_ok}/{mr.JSON_TRIES} · choices {res.choice_ok}/{len(mr._CHOICE_CASES)} · "
        f"{res.tokens_per_s} tok/s · {res.latency_s}s per reply")
    for e in res.errors[:3]:
        renderer.console.print(f"  [dim_text]{e}[/dim_text]")
    return res


def _rewire(engine) -> None:
    """Apply the new roles now: rebuild the AI wiring (no restart)."""
    from core.bootstrap import setup_ai
    setup_ai(engine)
    renderer.console.print(f"  [dim_text]AI {'online' if engine._ai_online() else 'offline'}: "
                           f"{getattr(engine, 'ai_models', {})}[/dim_text]")
