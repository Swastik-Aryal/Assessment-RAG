"""Local Ollama /api/chat client with retries and optional JSON schema output."""
import logging
import re
import time

import requests

log = logging.getLogger(__name__)
THINK = re.compile(r"<think>.*?</think>", re.S)


def _text(resp: dict) -> str:
    """Assistant content from an Ollama chat/generate payload, stripping think tags."""
    msg = resp.get("message") or {}
    raw = msg.get("content") or resp.get("response") or ""
    raw = THINK.sub("", raw).strip()
    if raw:
        return raw
    return (msg.get("thinking") or resp.get("thinking") or "").strip()


class OllamaClient:
    """Local /api/chat client with the same generate(system, user, schema) shape as Gemini."""

    def __init__(self, settings):
        self.settings = settings
        self.base = settings.OLLAMA_BASE_URL.rstrip("/")
        self.model = settings.OLLAMA_MODEL
        self.http = requests.Session()

    def generate(self, system: str, user: str, response_schema=None) -> str:
        """Chat completion. think=False; optional JSON schema via `format`."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }
        if response_schema is not None:
            payload["format"] = (
                response_schema.model_json_schema()
                if hasattr(response_schema, "model_json_schema")
                else response_schema
            )
        delay = 2
        for attempt in range(5):
            t0 = time.monotonic()
            try:
                r = self.http.post(
                    self.base + "/api/chat",
                    json=payload,
                    timeout=self.settings.OLLAMA_TIMEOUT_SECONDS,
                )
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(f"{r.status_code} {r.text[:200]}", response=r)
                r.raise_for_status()
                text = _text(r.json())
                log.debug("ollama %s %.1fs", self.model, time.monotonic() - t0)
                return text
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
                retryable = isinstance(e, (requests.Timeout, requests.ConnectionError)) or (
                    getattr(e, "response", None) is not None
                    and e.response.status_code in (429, 500, 502, 503, 504)
                )
                if not retryable or attempt == 4:
                    raise
                log.warning("ollama %s, retry in %ss", e, delay)
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

