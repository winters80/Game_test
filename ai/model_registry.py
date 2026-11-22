"""
Which Ollama model plays which role, and whether a better one is available.

Roles: ``primary`` (structured content), ``fast`` (interactive lines) and
``fallback`` (if the primary won't load). Each role resolves, first match wins:

1. an environment variable (``OLLAMA_MODEL``, ``OLLAMA_FAST_MODEL``,
   ``OLLAMA_FALLBACK_MODEL``)
2. ``saves/settings/ai_models.json``, written by Admin → « AI models »
3. the default in ``config.py``

So switching models is a menu choice, not a code change. The **catalog**
(``data/ai/model_catalog.json``) lists models the game knows, with the roles
they suit and the RAM they need. ``load_catalog`` also tries the newest copy
from the repository (``config.MODEL_CATALOG_URL``) and caches it, so new
recommendations reach every install by editing one JSON file on GitHub.

``review`` compares what's active with the catalog for this machine's RAM;
``benchmark`` gives a model a quick, game-shaped test (valid JSON, picking
the right option, speed) so a new model can be tried before it's trusted.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config

logger = logging.getLogger(__name__)

ROLES = ("primary", "fast", "fallback")
_ENV = {"primary": "OLLAMA_MODEL", "fast": "OLLAMA_FAST_MODEL", "fallback": "OLLAMA_FALLBACK_MODEL"}
_DEFAULTS = {"primary": "OLLAMA_MODEL", "fast": "OLLAMA_FAST_MODEL", "fallback": "OLLAMA_FALLBACK_MODEL"}

SETTINGS_DIR = config.SAVES_DIR / "settings"
SETTINGS_FILE = SETTINGS_DIR / "ai_models.json"
CATALOG_FILE = config.DATA_DIR / "ai" / "model_catalog.json"
CATALOG_CACHE = SETTINGS_DIR / "model_catalog_cache.json"


# ── Settings ──────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def resolve(role: str) -> tuple[str, str]:
    """(model, where it came from: 'env' / 'settings' / 'config')."""
    env = os.environ.get(_ENV[role])
    if env:
        return env, "env"
    chosen = (load_settings().get("roles") or {}).get(role)
    if chosen:
        return chosen, "settings"
    if role == "fast":
        return (config.OLLAMA_FAST_MODEL or config.OLLAMA_MODEL), "config"
    return getattr(config, _DEFAULTS[role]), "config"


def active_models() -> dict[str, str]:
    return {r: resolve(r)[0] for r in ROLES}


def set_role(role: str, model: str | None) -> None:
    """Choose ``model`` for ``role`` (None = back to the config default)."""
    if role not in ROLES:
        raise ValueError(role)
    data = load_settings()
    roles = data.setdefault("roles", {})
    if model:
        roles[role] = model
    else:
        roles.pop(role, None)
    data["changed"] = time.strftime("%Y-%m-%d %H:%M")
    save_settings(data)


def record_benchmark(result: "BenchResult") -> None:
    data = load_settings()
    data.setdefault("benchmarks", {})[result.model] = asdict(result)
    save_settings(data)


def benchmarks() -> dict[str, dict]:
    return load_settings().get("benchmarks", {})


# ── Catalog ───────────────────────────────────────────────────────────────────

def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _valid(cat: object) -> bool:
    return isinstance(cat, dict) and isinstance(cat.get("models"), list) and isinstance(cat.get("tiers"), list)


def fetch_remote_catalog(timeout: float = 5.0) -> dict | None:
    """Newest catalog from the repository; cached on success. None if offline."""
    url = getattr(config, "MODEL_CATALOG_URL", "")
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:      # noqa: S310 (fixed https URL)
            cat = json.loads(resp.read().decode("utf-8"))
        if not _valid(cat):
            return None
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        CATALOG_CACHE.write_text(json.dumps(cat, indent=2), encoding="utf-8")
        return cat
    except Exception as e:
        logger.info("Model catalog fetch failed: %s", e)
        return None


def load_catalog(remote: bool = False) -> tuple[dict, str]:
    """(catalog, source). The newest of: remote (if asked), cache, bundled."""
    options = []
    if remote:
        r = fetch_remote_catalog()
        if r:
            options.append((r, "online"))
    c = _read(CATALOG_CACHE)
    if _valid(c):
        options.append((c, "cached"))
    b = _read(CATALOG_FILE)
    if _valid(b):
        options.append((b, "bundled"))
    if not options:
        return {"models": [], "tiers": []}, "none"
    best = max(options, key=lambda o: (str(o[0].get("updated", "")), o[0].get("catalog_version", 0)))
    return best


def system_ram_gb() -> float:
    """Total physical memory in GB (0 if unknown)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (AttributeError, ValueError, OSError):
        pass
    try:                                    # Windows
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = _MS()
        ms.dwLength = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))   # type: ignore[attr-defined]
        return ms.ullTotalPhys / 1024 ** 3
    except Exception:
        return 0.0


def tier_for(catalog: dict, ram_gb: float) -> dict:
    tiers = sorted(catalog.get("tiers", []), key=lambda t: -t.get("min_ram_gb", 0))
    for t in tiers:
        if ram_gb >= t.get("min_ram_gb", 0) or ram_gb == 0:
            return t
    return tiers[-1] if tiers else {}


def model_info(catalog: dict, name: str) -> dict:
    from ai.ollama_client import model_in
    for m in catalog.get("models", []):
        if model_in(m["name"], {name}) or m["name"] == name:
            return m
    return {}


# ── Ollama housekeeping (so code outside ai/ never builds a client) ──────────

def installed_models() -> set[str]:
    """Models pulled into Ollama ("name:tag"); empty if Ollama is offline."""
    from ai.ollama_client import OllamaClient
    c = OllamaClient(model="probe", base_url=config.OLLAMA_BASE_URL)
    return c.installed_models() if c.is_available() else set()


def pull_model(model: str, on_progress=None) -> bool:
    """Download ``model`` into Ollama (``on_progress(status, done, total)``)."""
    from ai.ollama_client import OllamaClient
    return OllamaClient(model=model, base_url=config.OLLAMA_BASE_URL).pull(model, on_progress=on_progress)


# ── Review ────────────────────────────────────────────────────────────────────

@dataclass
class Suggestion:
    role: str
    current: str
    suggested: str
    installed: bool
    reason: str


def review(catalog: dict, active: dict[str, str], installed: set[str], ram_gb: float) -> list[Suggestion]:
    """Roles where the catalog recommends something better for this machine,
    or where the active model isn't installed. Benchmarks you've run count:
    a measured score beats the catalog's estimate."""
    from ai.ollama_client import model_in
    tier = tier_for(catalog, ram_gb)
    measured = benchmarks()
    out: list[Suggestion] = []

    def score(name: str) -> float:
        if name in measured and measured[name].get("score") is not None:
            return float(measured[name]["score"])
        return float(model_info(catalog, name).get("quality", 0))

    for role in ROLES:
        cur = active.get(role, "")
        want = tier.get(role, "")
        cur_installed = bool(cur) and model_in(cur, installed)
        if not want or want == cur:
            if cur and not cur_installed and installed:
                out.append(Suggestion(role, cur, cur, False, "the active model isn't downloaded yet"))
            continue
        cur_info, want_info = model_info(catalog, cur), model_info(catalog, want)
        need = want_info.get("min_ram_gb", 0)
        if ram_gb and need and ram_gb < need:
            continue
        if score(want) > score(cur) + 2 or not cur_info:
            why = (f"rated {want_info.get('quality', '?')} vs {cur_info.get('quality', '?')}"
                   if cur_info else "the current model isn't in the catalog")
            if want_info.get("notes"):
                why += f" · {want_info['notes']}"
            out.append(Suggestion(role, cur, want, model_in(want, installed), why))
    # Installed models newer than the catalog knows about are worth a benchmark.
    known = {m["name"] for m in catalog.get("models", [])}
    for name in sorted(installed):
        if not any(model_in(k, {name}) for k in known) and name not in measured \
                and not name.startswith(("nomic", "mxbai", "all-minilm")):
            out.append(Suggestion("?", "", name, True, "installed but not in the catalog: benchmark it"))
    return out


# ── Benchmark ─────────────────────────────────────────────────────────────────

@dataclass
class BenchResult:
    model: str
    json_ok: int = 0             # of JSON_TRIES
    choice_ok: int = 0           # of len(_CHOICE_CASES)
    latency_s: float = 0.0       # mean seconds for a short reply
    tokens_per_s: float = 0.0
    score: float | None = None   # 0-100
    when: str = ""
    errors: list[str] = field(default_factory=list)


JSON_TRIES = 3
_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "item_type": {"type": "string", "enum": ["MATERIAL", "CONSUMABLE"]},
        "value_gold": {"type": "integer"},
        "description": {"type": "string"},
    },
    "required": ["name", "item_type", "value_gold", "description"],
}
_CHOICE_CASES = [
    ("A player at 5 HP of 60 is fighting a boss. Which is the sensible action?",
     ["Drink a health potion", "Attack with a basic strike", "Inspect the boss's armour"], 0),
    ("A trader offers to buy wolf pelts. The player wants gold. What should they do with 6 wolf pelts?",
     ["Sell them to the trader", "Drop them on the floor", "Eat them"], 0),
    ("The gate guard says only ranked adventurers may pass. The player has no rank. Which option fits?",
     ["Clear the dungeon's first floor to earn a rank", "Walk through anyway, the guard is asleep forever",
      "Become the king of Verath instantly"], 0),
]


def benchmark(model: str, base_url: str | None = None) -> BenchResult:
    """A short, game-shaped test. Takes ~10-60 s depending on the model."""
    from ai.ollama_client import OllamaClient

    res = BenchResult(model=model, when=time.strftime("%Y-%m-%d %H:%M"))
    client = OllamaClient(model=model, base_url=base_url or config.OLLAMA_BASE_URL,
                          default_timeout=config.OLLAMA_TIMEOUT_JSON)
    if not client.is_available():
        res.errors.append("Ollama isn't answering")
        return res
    client.warm_up()

    for i in range(JSON_TRIES):
        try:
            data = client.generate_json(
                prompt=f"Invent crafting material #{i + 1} found in a crystal cave. Value 1-20 gold.",
                system_prompt="You write game data as JSON.", schema=_ITEM_SCHEMA,
                max_retries=1, num_predict=200, timeout=config.OLLAMA_TIMEOUT_JSON)
            if (isinstance(data, dict) and data.get("name") and data.get("item_type") in ("MATERIAL", "CONSUMABLE")
                    and isinstance(data.get("value_gold"), int)):
                res.json_ok += 1
        except Exception as e:
            res.errors.append(f"json: {e}"[:120])

    for q, opts, right in _CHOICE_CASES:
        schema = {"type": "object", "properties": {"choice": {"type": "integer", "enum": list(range(len(opts)))}},
                  "required": ["choice"]}
        listing = "\n".join(f"{i}: {o}" for i, o in enumerate(opts))
        try:
            data = client.generate_json(prompt=f"{q}\nOptions:\n{listing}\nAnswer with the option number.",
                                        system_prompt="You pick the best option.", schema=schema,
                                        max_retries=1, num_predict=20, temperature=0.0)
            if data.get("choice") == right:
                res.choice_ok += 1
        except Exception as e:
            res.errors.append(f"choice: {e}"[:120])

    times, rates = [], []
    for _ in range(2):
        before = client.token_summary().get("generated_tokens", 0)
        t0 = time.perf_counter()
        try:
            client.generate_text(prompt="Greet a fellow adventurer in one short sentence.",
                                 system_prompt="You are an adventurer.", max_tokens=40)
        except Exception as e:
            res.errors.append(f"text: {e}"[:120])
            continue
        dt = time.perf_counter() - t0
        times.append(dt)
        gen = client.token_summary().get("generated_tokens", 0) - before
        if gen and dt:
            rates.append(gen / dt)
    res.latency_s = round(sum(times) / len(times), 2) if times else 0.0
    res.tokens_per_s = round(sum(rates) / len(rates), 1) if rates else 0.0
    res.score = score_result(res)
    return res


def score_result(res: BenchResult) -> float:
    """0-100: JSON 45, choices 35, speed 20 (≥40 tok/s full marks)."""
    j = 45 * res.json_ok / JSON_TRIES
    c = 35 * res.choice_ok / len(_CHOICE_CASES)
    s = 20 * min(1.0, res.tokens_per_s / 40.0) if res.tokens_per_s else 0
    return round(j + c + s, 1)
