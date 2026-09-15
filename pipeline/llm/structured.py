"""Structured LLM output: generate JSON, validate against a Pydantic model, retry on failure."""
import json
import logging
import re

from pydantic import ValidationError

log = logging.getLogger(__name__)


class StructuredOutputError(Exception):
    """Raised after call_structured exhausts retries."""
    pass


def call_structured(llm, system, user, model_cls, max_attempts=3):
    """Generate JSON, validate as `model_cls`, feed the error back on failure."""
    msg = user
    for attempt in range(max_attempts):
        raw = llm.generate(system, msg, response_schema=model_cls)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        if not raw.startswith("{") and "{" in raw and "}" in raw:
            raw = raw[raw.find("{") : raw.rfind("}") + 1]
        try:
            return model_cls.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as e:
            log.warning("invalid structured output (attempt %s): %s", attempt + 1, e)
            msg = f"{user}\n\nYour previous reply was invalid: {e}\nReply with valid JSON only."
    raise StructuredOutputError(f"invalid output after {max_attempts} attempts")


if __name__ == "__main__":
    from typing import Literal

    from pydantic import BaseModel

    from pipeline.config.config import load_settings
    from pipeline.llm.gemini import GeminiClient
    from pipeline.config.logging_setup import setup_logging

    class Toy(BaseModel):
        name: str
        count: int
        status: Literal["ok", "no"]

    setup_logging("DEBUG")
    g = GeminiClient(load_settings())
    print(call_structured(g, "Return JSON matching the schema.", "name=widget, count=7, status=ok", Toy))
