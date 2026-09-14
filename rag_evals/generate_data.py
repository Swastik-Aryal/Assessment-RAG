"""Generate eval datasets: original KB mapping or synthetic rephrased questions.

Usage:
    python -m rag_evals.generate_data --original
    python -m rag_evals.generate_data --synthetic --k 2
"""
import argparse
import json
import logging
from pathlib import Path

from pydantic import BaseModel

from pipeline.config.config import load_settings
from pipeline.config.logging_setup import setup_logging
from pipeline.llm.ollama import OllamaClient
from pipeline.llm.structured import call_structured
from pipeline.rag.kb import load_kb

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent

SYSTEM_PROMPT = """You are a question-rephrasing tool for building a RAG evaluation dataset from security questionnaires.

TASK
Given a security question and its supporting context, produce rephrased questions that:
- Asks for the exact same information as the original question — same intent, same scope, same expected answer.
- Uses genuinely different wording: different sentence structure and vocabulary, and where natural, swap acronyms for their expansions or vice versa (e.g. "MFA" <-> "multi-factor authentication", "PII" <-> "personally identifiable information").
- Is fully self-contained: no pronouns or references that only make sense next to the original ("it", "this control", "the above requirement").
- Stays answerable using ONLY the given context — do not introduce facts, systems, or requirements not already present in the question or context.
- Does not broaden or narrow scope (e.g. don't turn a yes/no control question into a request for an implementation walkthrough; don't bundle in extra sub-questions).
- Keeps a professional, questionnaire-appropriate register.

DO NOT
- Copy phrases or sentences directly from the context into the question.
- Make only a trivial one- or two-word edit — the rewrite must differ meaningfully in phrasing.
- Change the question type (yes/no vs. open-ended) unless meaning is fully preserved.
- Add commentary, notes, or explanation outside the JSON output.

OUTPUT
Return ONLY valid JSON matching the schema below. No markdown, no code fences, no text before or after the JSON.
"""


class RephrasedQuestions(BaseModel):
    rephrasings: list[str]


def build_original(chunks) -> list[dict]:
    """One entry per chunk: the original Question Text mapped to original_id."""
    entries = []
    for c in chunks:
        for line in c.text.splitlines():
            if line.startswith("Question Text: "):
                entries.append({
                    "query": line[len("Question Text: "):],
                    "relevant_id": c.original_id,
                })
                break
    return entries


def generate_synthetic(chunks, llm, k: int) -> list[dict]:
    """For each chunk: include original question + k synthetic rephrasings (single LLM call)."""
    entries = []
    for idx, c in enumerate(chunks):
        question = ""
        for line in c.text.splitlines():
            if line.startswith("Question Text: "):
                question = line[len("Question Text: "):]
                break
        if not question:
            continue

        entries.append({"query": question, "relevant_id": c.original_id})

        user = (
            f"Context:\n{c.text}\n\n"
            f"Original question: {question}\n\n"
            f"Generate exactly {k} distinct rephrasings of this question. "
            f"Each must be different from the original and from each other."
        )
        rephrasings = []
        for attempt in range(3):
            try:
                result = call_structured(llm, SYSTEM_PROMPT, user, RephrasedQuestions)
                rephrasings = result.rephrasings[:k]
                if len(rephrasings) >= k:
                    break
                log.warning("chunk %s: got %d/%d rephrasings (attempt %d)", c.original_id, len(rephrasings), k, attempt + 1)
            except Exception as e:
                log.warning("failed rephrasing chunk %s (attempt %d): %s", c.original_id, attempt + 1, e)
        for r in rephrasings:
            entries.append({"query": r, "relevant_id": c.original_id})
        log.info("[%d/%d] chunk %s: %d rephrasings", idx + 1, len(chunks), c.original_id, len(rephrasings))

    return entries


def main():
    parser = argparse.ArgumentParser(description="Generate eval data")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--original", action="store_true", help="extract original KB questions")
    group.add_argument("--synthetic", action="store_true", help="generate synthetic rephrasings via Ollama")
    parser.add_argument("--k", type=int, default=2, help="synthetic rephrasings per chunk (default 2)")
    args = parser.parse_args()

    setup_logging()
    settings = load_settings()
    chunks = load_kb(settings.KB_PATH)

    if args.original:
        entries = build_original(chunks)
        out_path = DATA_DIR / "original.json"
    else:
        llm = OllamaClient(settings)
        entries = generate_synthetic(chunks, llm, k=args.k)
        out_path = DATA_DIR / "synthetic.json"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=2))
    print(f"wrote {len(entries)} entries to {out_path}")


if __name__ == "__main__":
    main()
