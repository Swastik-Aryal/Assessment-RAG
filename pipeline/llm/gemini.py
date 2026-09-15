"""Gemini API client with rate limiting, retries, and optional JSON schema output."""
import logging
import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

log = logging.getLogger(__name__)


class GeminiClient:
    """Gemini generate() with JSON schema, 429/5xx retry, and min-interval pacing."""

    def __init__(self, settings):
        self.settings = settings
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.last_call = 0.0

    def generate(self, system: str, user: str, response_schema=None) -> str:
        """Return model text. `response_schema` is a Pydantic class for JSON mode."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json" if response_schema else None,
            response_schema=response_schema,
        )
        delay = 5
        for attempt in range(5):
            wait = self.settings.GEMINI_MIN_INTERVAL_SECONDS - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            t0 = time.monotonic()
            try:
                resp = self.client.models.generate_content(
                    model=self.settings.GEMINI_MODEL, contents=user, config=config
                )
                self.last_call = time.monotonic()
                log.debug("gemini %.1fs usage=%s", self.last_call - t0, resp.usage_metadata)
                return resp.text or ""
            except (ServerError, ClientError) as e:
                self.last_call = time.monotonic()
                retryable = isinstance(e, ServerError) or e.code == 429
                if not retryable or attempt == 4:
                    raise
                log.warning("gemini %s, retry in %ss", e.code, delay)
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")
