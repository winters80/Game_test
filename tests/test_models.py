"""
Model roles, catalog review and benchmark scoring (ai/model_registry), and
that the game's AI wiring follows the chosen roles (core/bootstrap).

    python tests/test_models.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓  " if cond else "  ✗  ") + msg)
    if not cond:
        failures.append(msg)


def main() -> None:
    from ai import model_registry as mr

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patches = [mock.patch.object(mr, "SETTINGS_DIR", tmp),
                   mock.patch.object(mr, "SETTINGS_FILE", tmp / "ai_models.json"),
                   mock.patch.object(mr, "CATALOG_CACHE", tmp / "cache.json")]
        for p in patches:
            p.start()
        env = {k: v for k, v in os.environ.items()
               if k not in ("OLLAMA_MODEL", "OLLAMA_FAST_MODEL", "OLLAMA_FALLBACK_MODEL")}
        try:
            with mock.patch.dict(os.environ, env, clear=True):
                import config
                check(mr.resolve("primary") == (config.OLLAMA_MODEL, "config"), "defaults come from config.py")
                mr.set_role("primary", "qwen3:14b")
                check(mr.resolve("primary") == ("qwen3:14b", "settings"), "a chosen model overrides config")
                with mock.patch.dict(os.environ, {"OLLAMA_MODEL": "mine:7b"}):
                    check(mr.resolve("primary") == ("mine:7b", "env"), "an environment variable wins")
                mr.set_role("primary", None)
                check(mr.resolve("primary")[1] == "config", "reset goes back to config")

                cat, src = mr.load_catalog()
                check(src == "bundled" and len(cat["models"]) >= 5, "the bundled catalog loads")
                newer = dict(cat, updated="2099-01-01", tiers=cat["tiers"])
                (tmp / "cache.json").write_text(json.dumps(newer))
                check(mr.load_catalog()[1] == "cached", "a newer cached (online) catalog wins")
                (tmp / "cache.json").unlink()
                check(all(m["name"] for m in cat["models"]) and
                      {t["primary"] for t in cat["tiers"]} <= {m["name"] for m in cat["models"]},
                      "every tier recommends a model the catalog describes")

                # Review: an older primary on a 16 GB machine → suggest the tier's pick.
                mr.set_role("primary", "gemma3:12b")
                active = mr.active_models()
                tips = mr.review(cat, active, {"gemma3:12b", "qwen3.5:4b", "qwen3.5:9b"}, 16)
                prim = [t for t in tips if t.role == "primary"]
                check(prim and prim[0].suggested == "gemma4:12b" and not prim[0].installed,
                      f"review suggests a better primary for 16 GB ({[t.__dict__ for t in tips]})")
                tips = mr.review(cat, active, {"gemma3:12b"}, 8)
                check(not [t for t in tips if t.role == "primary" and t.suggested == "gemma4:12b"],
                      "no suggestion that needs more memory than the machine has")
                tips = mr.review(cat, mr.active_models(), {"gemma3:12b", "brandnew:20b"}, 16)
                check(any(t.role == "?" and t.suggested == "brandnew:20b" for t in tips),
                      "an installed model the catalog doesn't know is offered for a benchmark")
                # A measured benchmark beats the catalog estimate.
                mr.record_benchmark(mr.BenchResult(model="gemma3:12b", score=99.0))
                tips = mr.review(cat, mr.active_models(), {"gemma3:12b", "gemma4:12b"}, 16)
                check(not [t for t in tips if t.role == "primary"], "a high benchmark keeps the current model")
                mr.set_role("primary", None)

                r = mr.BenchResult(model="m", json_ok=3, choice_ok=3, tokens_per_s=40)
                check(mr.score_result(r) == 100, "a perfect run scores 100")
                r = mr.BenchResult(model="m", json_ok=0, choice_ok=0, tokens_per_s=0)
                check(mr.score_result(r) == 0, "a model that does nothing scores 0")
                check(mr.system_ram_gb() >= 0, "memory detection doesn't crash")

                # setup_ai uses the chosen roles (and the fallback if the primary is missing).
                from core import bootstrap

                class FakeClient:
                    installed = set()

                    def __init__(self, model, base_url="", default_timeout=None):
                        self.model = model

                    def is_available(self):
                        return True

                    def installed_models(self):
                        return set(FakeClient.installed)

                    def has_model(self, m=None):
                        from ai.ollama_client import model_in
                        return model_in(m or self.model, FakeClient.installed)

                    def warm_up(self):
                        return True

                class Eng:
                    lore_data, _bg_generator, _world_director = {}, None, None

                mr.set_role("primary", "qwen3.5:9b")
                mr.set_role("fast", "qwen3.5:4b")
                FakeClient.installed = {"qwen3.5:9b", "qwen3.5:4b"}
                with mock.patch("ai.ollama_client.OllamaClient", FakeClient), \
                        mock.patch.object(bootstrap, "_preload_models", lambda *a: None), \
                        mock.patch.dict(config.FEATURES, {"world_db": False}), \
                        mock.patch.object(bootstrap.renderer, "print_success", lambda *a: None):
                    e = Eng()
                    bootstrap.setup_ai(e)
                    check(e.ai_service.is_available and e.ai_models["primary"] == "qwen3.5:9b",
                          "the game wires the chosen primary without a code change")
                    mr.set_role("primary", "gemma4:12b")
                    mr.set_role("fallback", "qwen3.5:9b")
                    with mock.patch.object(bootstrap, "_confirm_download", lambda m: False):
                        e = Eng()
                        bootstrap.setup_ai(e)
                    check(e.ai_service.is_available and e.ai_models["primary"] == "qwen3.5:9b",
                          "a missing primary falls back to the fallback model")
        finally:
            for p in patches:
                p.stop()


def decisions() -> None:
    from ai import decision as dc
    from ai.ai_service import AIService

    check(dc._parse({"choice": 1, "confidence": 80}, 3, "t").index == 1, "a decision parses")
    check(dc._parse({"choice": 5, "confidence": 80}, 3, "t") is None, "an out-of-range choice is rejected")
    check(dc._parse("nope", 3, "t") is None, "garbage is rejected")

    class FakeClient:
        model = "fake"

        def __init__(self, answer):
            self.answer, self.calls = answer, []

        def generate_json(self, **kw):
            self.calls.append(kw)
            return self.answer

    fc = FakeClient({"choice": 2, "confidence": 90})
    d = dc.OllamaDecider(fc).decide("Which?", ["a", "b", "c"])
    check(d.index == 2 and abs(d.confidence - 0.9) < 1e-9, "the Ollama decider returns the choice")
    check(fc.calls[0]["schema"]["properties"]["choice"]["enum"] == [0, 1, 2],
          "options are declared up front as a schema enum")

    class Gen:
        def decide(self, q, o, c=""):
            raise RuntimeError("boom")
    check(AIService(content_generator=Gen()).decide("q", ["a"]) is None, "a failing decision returns None")
    check(AIService().decide("q", ["a"]) is None, "offline → None (callers use their rules)")

    # [?]: typed text that matches an existing choice just takes it.
    from core import situation_query as sq
    from scenes.scene_base import SceneOption

    class Svc:
        is_available = True

        def __init__(self, d):
            self.d = d

        def decide(self, q, labels, context=""):
            self.labels = labels
            return self.d

    opts = [SceneOption(option_id="inn", label="Visit the Iron Compass Inn.", leads_to="__stay__"),
            SceneOption(option_id="forge", label="Visit the blacksmith's forge.", leads_to="__stay__")]
    svc = Svc(dc.Decision(0, 0.95, "t"))
    with mock.patch.object(sq.renderer, "show_ai_thinking_spinner", lambda *a: mock.MagicMock()):
        got = sq._match_existing(None, svc, "go to the inn", opts)
        check(got is opts[0] and svc.labels[-1] == sq._NONE, "'go to the inn' takes the inn option")
        check(sq._match_existing(None, Svc(dc.Decision(2, 0.95, "t")), "dance", opts) is None,
              "'none of these' falls through to the full answer")
        check(sq._match_existing(None, Svc(dc.Decision(0, 0.3, "t")), "maybe inn", opts) is None,
              "a low-confidence match falls through")
        check(sq._match_existing(None, svc, "is the inn open?", opts) is None,
              "questions always get a real answer")


if __name__ == "__main__":
    main()
    decisions()
    print()
    if failures:
        print(f"  {len(failures)} model check(s) failed.")
        sys.exit(1)
    print("  All model checks passed.")
