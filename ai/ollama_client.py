from __future__ import annotations

import json
import logging
from typing import Any

from config import OLLAMA_KEEP_ALIVE, OLLAMA_WARMUP_TIMEOUT

logger = logging.getLogger(__name__)


def model_in(model: str, installed: set[str]) -> bool:
    """Ollama treats "name" and "name:latest" as the same model."""
    want = model if ":" in model else f"{model}:latest"
    return model in installed or want in installed


class OllamaTimeoutError(Exception):
    pass


class OllamaParseError(Exception):
    pass


class OllamaClient:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434",
                 default_timeout: int = 120) -> None:
        self.model = model
        self.base_url = base_url
        self._client = None
        self._client_timeout: int = default_timeout
        self._default_timeout: int = default_timeout
        # Cumulative token counters for this session
        self.tokens_prompt: int = 0
        self.tokens_generated: int = 0
        self.calls_made: int = 0

    def token_summary(self) -> dict:
        return {
            "calls": self.calls_made,
            "prompt_tokens": self.tokens_prompt,
            "generated_tokens": self.tokens_generated,
            "total_tokens": self.tokens_prompt + self.tokens_generated,
        }

    def _record_usage(self, response: object) -> None:
        """Extract and accumulate token counts from an Ollama response."""
        try:
            if isinstance(response, dict):
                self.tokens_prompt += response.get("prompt_eval_count", 0)
                self.tokens_generated += response.get("eval_count", 0)
            else:
                self.tokens_prompt += getattr(response, "prompt_eval_count", 0) or 0
                self.tokens_generated += getattr(response, "eval_count", 0) or 0
            self.calls_made += 1
        except Exception:
            pass

    def _get_client(self, timeout: int | None = None):
        """Return (or create) an ollama.Client. If timeout differs from cached, creates a new one."""
        desired = timeout if timeout is not None else self._default_timeout
        if self._client is None or self._client_timeout != desired:
            try:
                import ollama
                import httpx
                self._client = ollama.Client(host=self.base_url, timeout=httpx.Timeout(desired))
                self._client_timeout = desired
            except ImportError:
                raise RuntimeError("ollama package not installed. Run: pip install ollama")
        return self._client

    def is_available(self) -> bool:
        try:
            client = self._get_client()  # uses default timeout
            client.list()
            return True
        except Exception:
            return False

    def installed_models(self) -> set[str]:
        """Names of models pulled into this Ollama server ("name:tag" form).
        Empty set if the server can't be reached."""
        try:
            listing = self._get_client().list()
        except Exception:
            return set()
        models = listing.get("models", []) if isinstance(listing, dict) else getattr(listing, "models", [])
        names = set()
        for m in models or []:
            name = m.get("model") or m.get("name") if isinstance(m, dict) else (
                getattr(m, "model", None) or getattr(m, "name", None))
            if name:
                names.add(str(name))
        return names

    def has_model(self, model: str | None = None) -> bool:
        """True if ``model`` (default: this client's model) is pulled.
        A bare name matches its ``:latest`` tag, as Ollama does."""
        return model_in(model or self.model, self.installed_models())

    def warm_up(self) -> bool:
        """Load the model into memory now (and keep it for OLLAMA_KEEP_ALIVE),
        so the first real request doesn't wait for a multi-GB load."""
        try:
            self._get_client(timeout=OLLAMA_WARMUP_TIMEOUT).generate(
                model=self.model, prompt="", keep_alive=OLLAMA_KEEP_ALIVE,
            )
            return True
        except Exception as e:
            logger.info("Warm-up of %s failed: %s", self.model, e)
            return False

    def generate_json(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.4,
        timeout: int = 120,
        max_retries: int = 3,
        num_predict: int = 800,
    ) -> dict[str, Any]:
        client = self._get_client(timeout)
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                logger.debug(
                    "generate_json attempt=%d model=%s temp=%.2f prompt_chars=%d num_predict=%d",
                    attempt + 1, self.model, temperature, len(prompt), num_predict,
                )
                response = client.generate(
                    model=self.model,
                    prompt=prompt,
                    system=system_prompt,
                    format="json",
                    options={
                        "temperature": temperature,
                        "num_predict": num_predict,
                    },
                    keep_alive=OLLAMA_KEEP_ALIVE,
                )
                raw = response.get("response", "") if isinstance(response, dict) else response.response
                data = json.loads(raw)
                self._record_usage(response)
                logger.debug(
                    "generate_json OK  keys=%s  prompt_tokens=%d  gen_tokens=%d",
                    list(data.keys())[:6],
                    getattr(response, "prompt_eval_count", 0) or 0,
                    getattr(response, "eval_count", 0) or 0,
                )
                return data
            except json.JSONDecodeError as e:
                last_error = OllamaParseError(f"JSON parse failed on attempt {attempt + 1}: {e}")
                # Per-retry: INFO only (goes to ai.log, NOT the player's terminal).
                # The retry will absorb most transient failures; the player
                # doesn't need to see every attempt. Only the final exhaustion
                # below is loud enough to break the player's immersion.
                logger.info("generate_json parse error attempt=%d: %s", attempt + 1, e)
            except Exception as e:
                last_error = e
                logger.info("generate_json request error attempt=%d: %s", attempt + 1, e)

        # Demoted from ERROR → WARNING. The caller decides what to do on
        # exhaustion (fall back gracefully vs propagate). Players don't need
        # to see "ERROR" in their terminal for a routine retry-exhausted
        # path that has a clean fallback. Real failures (the one that the
        # caller has no graceful path for) should be reported by the caller.
        logger.warning("generate_json exhausted all %d attempt(s)", max_retries)
        raise last_error or OllamaParseError("All retries exhausted")

    def generate_text(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.85,
        max_tokens: int = 400,
        timeout: int = 120,
    ) -> str:
        client = self._get_client(timeout)
        logger.debug(
            "generate_text model=%s temp=%.2f max_tokens=%d prompt_chars=%d",
            self.model, temperature, max_tokens, len(prompt),
        )
        try:
            response = client.generate(
                model=self.model,
                prompt=prompt,
                system=system_prompt,
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                keep_alive=OLLAMA_KEEP_ALIVE,
            )
            raw = response.get("response", "") if isinstance(response, dict) else response.response
            self._record_usage(response)
            logger.debug("generate_text OK  chars=%d", len(raw))
            return raw.strip()
        except Exception as e:
            logger.error("generate_text failed: %s", e)
            raise
