"""
ai/llm_provider.py
------------------
Abstraction layer for the local LLM.
Communicates with Ollama (or any compatible local server) via HTTP.
The CRM never calls Ollama-specific code directly — always uses this module.
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

# ── Configuration (read from .env) ─────────────────────────
AI_ENABLED = os.environ.get("AI_ENABLED", "true").lower() == "true"
AI_BASE_URL = os.environ.get("AI_BASE_URL", "http://127.0.0.1:11434")
AI_MODEL    = os.environ.get("AI_MODEL", "mistral")
AI_TIMEOUT  = int(os.environ.get("AI_TIMEOUT", "30"))


class LLMUnavailableError(Exception):
    pass


class LocalLLMProvider:
    """
    Thin wrapper around Ollama's /api/chat endpoint.
    Replace this class to swap in a different LLM backend.
    """

    def __init__(self, base_url: str = AI_BASE_URL, model: str = AI_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model    = model

    def _is_healthy(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        """
        messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
        Returns the assistant text response.
        """
        if not AI_ENABLED:
            raise LLMUnavailableError("AI is disabled via configuration.")

        if not self._is_healthy():
            raise LLMUnavailableError("Local LLM is not reachable.")

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=AI_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"].strip()
        except LLMUnavailableError:
            raise
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            raise LLMUnavailableError(f"LLM error: {e}")

    def generate(self, prompt: str, temperature: float = 0.2) -> str:
        """Simple single-prompt generate (wraps chat)."""
        return self.chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
        )


# Singleton — one LLM provider per process
_llm = None


def get_llm() -> LocalLLMProvider:
    global _llm
    if _llm is None:
        _llm = LocalLLMProvider()
    return _llm
