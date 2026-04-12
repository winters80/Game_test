from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class OllamaTimeoutError(Exception):
    pass


class OllamaParseError(Exception):
    pass


class OllamaClient:
    def __init__(self, model: str, base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self.base_url)
            except ImportError:
                raise RuntimeError("ollama package not installed. Run: pip install ollama")
        return self._client

    def is_available(self) -> bool:
        try:
            client = self._get_client()
            client.list()
            return True
        except Exception:
            return False

    def generate_json(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.4,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                response = client.generate(
                    model=self.model,
                    prompt=prompt,
                    system=system_prompt,
                    format="json",
                    options={
                        "temperature": temperature,
                        "num_predict": 800,
                    },
                )
                raw = response.get("response", "") if isinstance(response, dict) else response.response
                data = json.loads(raw)
                return data
            except json.JSONDecodeError as e:
                last_error = OllamaParseError(f"JSON parse failed on attempt {attempt + 1}: {e}")
                logger.warning(str(last_error))
            except Exception as e:
                last_error = e
                logger.warning(f"Ollama request failed on attempt {attempt + 1}: {e}")

        raise last_error or OllamaParseError("All retries exhausted")

    def generate_text(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.85,
        max_tokens: int = 400,
        timeout: int = 20,
    ) -> str:
        client = self._get_client()
        response = client.generate(
            model=self.model,
            prompt=prompt,
            system=system_prompt,
            options={
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        )
        raw = response.get("response", "") if isinstance(response, dict) else response.response
        return raw.strip()
