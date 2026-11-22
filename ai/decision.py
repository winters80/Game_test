"""
Decisions: pick one of a fixed set of options instead of writing text.

A decision call declares the options up front and gets back an index (and a
rough confidence). It is one short, schema-constrained call, so it is much
faster and cheaper than asking a model to write an answer and then parsing
it. Dedicated decision models (e.g. Microsoft-Decision-1, which returns a
probability per option) do exactly this; a small local model with a JSON enum
schema does a good-enough version offline.

Providers (``config.DECISION_PROVIDER``):

- ``"ollama"`` (default): the fast local model, JSON schema
  ``{"choice": <enum of indices>, "confidence": 0-100}``.
- ``"openai_compatible"``: any HTTPS endpoint that speaks the OpenAI chat
  completions API with ``response_format: json_schema`` (OpenRouter,
  Azure AI Foundry, a local llama.cpp / vLLM server …). Set
  ``DECISION_ENDPOINT``, ``DECISION_MODEL`` and the key in the
  ``DECISION_API_KEY`` environment variable. Off unless you opt in, because
  it sends the scene text to that service.
- ``"off"``: no decisions; callers use their rules.

Callers go through ``AIService.decide`` / ``decide_many``, which return
``None`` on any failure so the game falls back to its own rules.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    index: int                 # chosen option
    confidence: float          # 0-1 (a model's own estimate; treat as rough)
    source: str                # provider that answered


def _schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {"choice": {"type": "integer", "enum": list(range(n))},
                       "confidence": {"type": "integer", "minimum": 0, "maximum": 100}},
        "required": ["choice", "confidence"],
    }


def _prompt(question: str, options: list[str], context: str) -> str:
    listing = "\n".join(f"{i}: {o}" for i, o in enumerate(options))
    ctx = f"Context: {context}\n" if context else ""
    return (f"{ctx}Question: {question}\nOptions:\n{listing}\n"
            "Answer with the number of the best option and how confident you are (0-100).")


_SYSTEM = ("You are a decision function inside a game. You never write prose. "
           "You pick exactly one of the numbered options.")


def _parse(data: object, n: int, source: str) -> Decision | None:
    if not isinstance(data, dict):
        return None
    try:
        idx = int(data.get("choice"))
    except (TypeError, ValueError):
        return None
    if not 0 <= idx < n:
        return None
    try:
        conf = float(data.get("confidence", 50)) / 100.0
    except (TypeError, ValueError):
        conf = 0.5
    return Decision(idx, max(0.0, min(1.0, conf)), source)


class OllamaDecider:
    """Decisions on a local Ollama model (the fast client)."""

    def __init__(self, client) -> None:
        self.client = client

    def decide(self, question: str, options: list[str], context: str = "") -> Decision | None:
        from config import OLLAMA_TIMEOUT_FAST
        data = self.client.generate_json(
            prompt=_prompt(question, options, context), system_prompt=_SYSTEM,
            schema=_schema(len(options)), temperature=0.0, max_retries=1,
            num_predict=30, timeout=OLLAMA_TIMEOUT_FAST)
        return _parse(data, len(options), f"ollama:{self.client.model}")


class OpenAICompatibleDecider:
    """Decisions from an OpenAI-compatible endpoint (opt-in, see module doc)."""

    def __init__(self, endpoint: str, model: str, api_key: str, timeout: float = 15.0) -> None:
        self.endpoint, self.model, self.api_key, self.timeout = endpoint.rstrip("/"), model, api_key, timeout

    def decide(self, question: str, options: list[str], context: str = "") -> Decision | None:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": _prompt(question, options, context)}],
            "temperature": 0,
            "max_tokens": 30,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "decision", "schema": _schema(len(options))}},
        }
        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:      # noqa: S310 (configured URL)
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return _parse(json.loads(content), len(options), f"remote:{self.model}")


def build_decider(fast_client):
    """The configured decider, or None (``DECISION_PROVIDER = "off"``)."""
    import config
    provider = getattr(config, "DECISION_PROVIDER", "ollama")
    if provider == "off":
        return None
    if provider == "openai_compatible":
        key = os.environ.get("DECISION_API_KEY", "")
        endpoint = getattr(config, "DECISION_ENDPOINT", "")
        model = getattr(config, "DECISION_MODEL", "")
        if key and endpoint and model:
            return OpenAICompatibleDecider(endpoint, model, key)
        logger.info("DECISION_PROVIDER is openai_compatible but endpoint/model/key are missing; using Ollama")
    return OllamaDecider(fast_client) if fast_client is not None else None
