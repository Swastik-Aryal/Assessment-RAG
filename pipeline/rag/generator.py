"""RAG answer generation: build prompts, call LLM, finalize with enum correction and source validation."""
import argparse
import json
import logging
from typing import Literal

from pydantic import Field, create_model

from pipeline.llm.structured import call_structured
from pipeline.models import (
    FieldFormat,
    FillTarget,
    GeneratedAnswer,
    Question,
)
from pipeline.rag.index import Hit

log = logging.getLogger(__name__)

CORE = frozenset({"ans_status", "confidence", "answer", "sources", "extra"})

RULES = """You answer security questionnaire questions on behalf of the vendor.
Use only the provided knowledge-base rows.

Always fill these four keys first, in this order:
- ans_status: answerable when a row directly answers the question; needs_review when a row is on the same control but only partly answers, or any part of a multi-part question is unsupported; unanswerable when no row answers the question.
- confidence: high only for a near-paraphrase match, and never unless ans_status=answerable. Otherwise medium or low.
- answer: the answer to the question. Follow the answer-column rule and allowed values when given.
- sources: the id values shown on retrieved rows you actually used. Empty list if you used none. Never cite anything not shown as [id=...].

Then fill any extra workbook keys listed below. Follow each column's rule.
IMPORTANT: If you are asked to fill an enum column, you must use the exact spelling from the allowed values list. Do not invent or modify the spelling.
Always cite the retrieved rows you actually used in any comment/description/explanatory field, and in answer if that column has no restriction.
"""


def _canon(val: str, allowed: list[str]) -> str | None:
    """Map a model enum string onto the canonical allowed spelling, or None."""
    key = val.strip().lower()
    for a in allowed:
        if a.strip().lower() == key:
            return a
    return None


def extra_key(t: FillTarget) -> str:
    """JSON key for a non-answer llm column. Prefer target_id when it is a valid field name."""
    k = t.target_id
    if k.isidentifier() and k not in CORE:
        return k
    return "col_" + "".join(c if c.isalnum() else "_" for c in k)


def llm_answer_model(targets: list[FillTarget]):
    """Core four fields first, then one string per extra llm fill_target."""
    fields = {
        "ans_status": (
            Literal["answerable", "needs_review", "unanswerable"],
            Field(description="Always fill first. answerable | needs_review | unanswerable"),
        ),
        "confidence": (
            Literal["high", "medium", "low"],
            Field(description="Always fill. high | medium | low"),
        ),
        "answer": (str, Field(description="Always fill. The answer to the question.")),
        "sources": (
            list[str],
            Field(description="Always fill. Retrieved row ids you used; empty list if none."),
        ),
    }
    for t in targets:
        if t.strategy != "llm" or t.role == "answer":
            continue
        desc = f"Table column, fill after the four core keys if you have something to write. {t.header or t.target_id}"
        if t.rule:
            desc += ". " + t.rule
        if t.format.type == "enum" and t.format.allowed_values:
            desc += " Allowed values: " + ", ".join(t.format.allowed_values)
        fields[extra_key(t)] = (str, Field(default="", description=desc))
    return create_model("LlmAnswer", **fields)


def build_system(targets: list[FillTarget], instructions: str = "") -> str:
    """Core four keys first, then each extra llm column's json key, format, and rule."""
    lines = [
        RULES,
        "",
        "Core JSON keys (always fill first): ans_status, confidence, answer, sources",
    ]
    for t in targets:
        if t.strategy != "llm" or t.role != "answer":
            continue
        line = f"answer: write into {t.header or t.target_id}"
        if t.format.type == "enum" and t.format.allowed_values:
            line += "; allowed values: " + ", ".join(t.format.allowed_values)
        if t.rule:
            line += "; rule: " + t.rule
        lines.append(line)
    extras = [t for t in targets if t.strategy == "llm" and t.role != "answer"]
    if extras:
        lines.append("")
        lines.append("Then extra workbook columns:")
        for t in extras:
            line = f"- {extra_key(t)} ({t.role}, {t.header or t.target_id})"
            if t.format.type == "enum" and t.format.allowed_values:
                line += "; allowed values: " + ", ".join(t.format.allowed_values)
            if t.rule:
                line += "; rule: " + t.rule
            lines.append(line)
    if instructions:
        lines += ["", "Workbook instructions:", instructions]
    return "\n".join(lines)


def _norm_cite(s: str) -> str:
    """Strip [id=...] / id= wrappers the model sometimes copies from the prompt."""
    s = s.strip().strip("[]")
    if s.lower().startswith("id="):
        s = s[3:].strip().rstrip("]")
    return s


def _cite_map(hits: list[Hit]) -> dict[str, str]:
    """Map any id on a hit (original_id or internal id) to original_id."""
    m = {}
    for h in hits:
        oid = (h.chunk.original_id or "").strip()
        cid = (h.chunk.id or "").strip()
        if oid:
            m[oid] = oid
        if cid:
            m[cid] = oid or cid
    return m


def build_user(question: Question, hits: list[Hit]) -> str:
    """User prompt: question plus retrieved rows tagged with original_id only."""
    parts = [f"Question id: {question.id}", f"Question: {question.text}", "", "Retrieved rows:"]
    if not hits:
        parts.append("(none)")
    for h in hits:
        oid = (h.chunk.original_id or "").strip()
        if oid:
            parts.append(f"[id={oid}] score={h.score:.3f}")
        else:
            parts.append(f"[score={h.score:.3f}]")
        parts.append(h.chunk.text)
        parts.append("")
    parts.append("Fill ans_status, confidence, answer, sources first, then any extra keys.")
    return "\n".join(parts).rstrip()


def _enum_misses(raw, targets: list[FillTarget]) -> list[tuple[str, str, list[str]]]:
    """Enum llm fields whose value is not in allowed_values (case-insensitive)."""
    data = raw.model_dump()
    nested = data.pop("extra", None) or {}
    misses = []
    for t in targets:
        if t.strategy != "llm" or t.format.type != "enum" or not t.format.allowed_values:
            continue
        if t.role == "answer":
            val, name = str(data.get("answer") or "").strip(), "answer"
        else:
            k = extra_key(t)
            val = data.get(k, nested.get(t.target_id, nested.get(k, "")))
            val = "" if val is None else str(val).strip()
            name = k
        if _canon(val, t.format.allowed_values):
            continue
        if not val:
            continue
        misses.append((name, val, t.format.allowed_values))
    return misses


def _enum_retry_user(user: str, misses: list[tuple[str, str, list[str]]]) -> str:
    lines = [
        user,
        "",
        "Your previous JSON used values that are not allowed. Reply with the same JSON keys.",
        "Each listed field must be exactly one of the allowed values (same spelling):",
    ]
    for name, got, allowed in misses:
        lines.append(f"- {name}: you wrote {got!r}; allowed: {', '.join(allowed)}")
    return "\n".join(lines)


def finalize(ans, hits: list[Hit], targets: list[FillTarget]) -> GeneratedAnswer:
    """Keep retrieved citations, canonical enum spelling, extra columns, honest confidence."""
    data = ans.model_dump()
    nested = data.pop("extra", None) or {}
    cites = _cite_map(hits)
    orig = []
    for s in data.get("sources") or []:
        oid = cites.get(_norm_cite(str(s)))
        if oid and oid not in orig:
            orig.append(oid)
    status = data["ans_status"]
    confidence = data["confidence"]
    if status != "answerable" and confidence == "high":
        confidence = "medium"
    answer = str(data.get("answer") or "").strip()
    answer_t = next((t for t in targets if t.role == "answer"), None)
    if answer_t and answer_t.format.type == "enum" and answer_t.format.allowed_values:
        canon = _canon(answer, answer_t.format.allowed_values)
        if canon:
            answer = canon
    extra = {}
    for t in targets:
        if t.strategy != "llm" or t.role == "answer":
            continue
        key = extra_key(t)
        val = data.get(key, nested.get(t.target_id, nested.get(key, "")))
        val = "" if val is None else str(val).strip()
        if t.format.type == "enum" and t.format.allowed_values:
            canon = _canon(val, t.format.allowed_values)
            if canon:
                val = canon
        extra[t.target_id] = val
    return GeneratedAnswer(
        ans_status=status,
        confidence=confidence,
        answer=answer,
        sources=orig,
        extra=extra,
    )


def portal_text(ans: GeneratedAnswer) -> str:
    """Portal has one box; fold the four model fields into it."""
    cites = ", ".join(ans.sources) if ans.sources else "none"
    return f"{ans.answer} [{ans.ans_status}; Confidence: {ans.confidence}; KB: {cites}]".strip()


def cell_values(ans: GeneratedAnswer, targets: list[FillTarget]) -> dict[str, str]:
    """Map model output onto schema columns: answer plus each extra llm target."""
    out = {}
    for t in targets:
        if t.strategy != "llm":
            continue
        if t.role == "answer":
            out[t.target_id] = ans.answer
        else:
            out[t.target_id] = ans.extra.get(t.target_id, "")
    return out


class Generator:
    """Retrieve, prompt, structured generate, then finalize."""

    def __init__(self, llm, retriever):
        self.llm = llm
        self.retriever = retriever

    def answer(self, question: Question, targets: list[FillTarget], instructions: str = ""):
        """Return (finalized answer, hits, system prompt, user prompt, raw llm json)."""
        hits = self.retriever.retrieve(question.text)
        system = build_system(targets, instructions)
        user = build_user(question, hits)
        model = llm_answer_model(targets)
        raw = call_structured(self.llm, system, user, model)
        misses = _enum_misses(raw, targets)
        if misses:
            log.warning("enum mismatch, retrying once: %s", misses)
            raw = call_structured(
                self.llm, system, _enum_retry_user(user, misses), model, max_attempts=1
            )
        return finalize(raw, hits, targets), hits, system, user, raw.model_dump()


def _portal_target() -> FillTarget:
    """Portal has no workbook schema; one free-text answer, then portal_text combines fields."""
    return FillTarget(
        target_id="answer",
        header="Answer",
        role="answer",
        format=FieldFormat(type="free_text"),
    )


def _check_finalize():
    """Self-check: enum spelling, extra columns, portal combine."""
    from pipeline.rag.kb import Chunk
    from pipeline.rag.index import Hit

    chunk = Chunk("c1", "text", "OID-1", "s", "c")
    hits = [Hit(chunk, 0.9)]
    enum_t = FillTarget(
        target_id="a",
        header="Answer",
        role="answer",
        format=FieldFormat(
            type="enum",
            allowed_values=["Yes", "No", "Partial", "Not applicable"],
        ),
    )
    comment = FillTarget(
        target_id="c",
        header="Comment",
        role="comment",
        format=FieldFormat(type="free_text"),
    )
    appl = FillTarget(
        target_id="ni_applicable",
        header="Applicable (Y/N)",
        role="applicability",
        format=FieldFormat(type="enum", allowed_values=["Yes", "No"]),
    )
    evidence = FillTarget(
        target_id="ni_evidence",
        header="Evidence reference",
        role="evidence",
        format=FieldFormat(type="free_text"),
    )
    M = llm_answer_model([enum_t, comment, appl, evidence])
    schema = M.model_json_schema()
    assert list(schema["properties"])[:4] == ["ans_status", "confidence", "answer", "sources"]
    assert set(schema["properties"]) == {
        "ans_status", "confidence", "answer", "sources", "c", "ni_applicable", "ni_evidence"
    }
    sys = build_system([enum_t, comment, appl, evidence])
    assert sys.index("ans_status") < sys.index("ni_applicable")
    assert "always fill first" in sys.lower()

    raw = M(
        ans_status="answerable",
        confidence="high",
        answer="yes",
        sources=["OID-1"],
        c="MFA is required",
        ni_applicable="yes",
        ni_evidence="SOC2 18.1",
    )
    out = finalize(raw, hits, [enum_t, comment, appl, evidence])
    assert out.answer == "Yes", out.answer
    assert out.sources == ["OID-1"]
    assert out.extra["c"] == "MFA is required"
    assert out.extra["ni_applicable"] == "Yes"
    cells = cell_values(out, [enum_t, comment, appl, evidence])
    assert cells["a"] == "Yes"
    assert cells["c"] == "MFA is required"
    assert cells["ni_applicable"] == "Yes"
    assert cells["ni_evidence"] == "SOC2 18.1"

    out = finalize(
        GeneratedAnswer(ans_status="unanswerable", confidence="high", answer="n/a", sources=[]),
        hits,
        [enum_t, comment],
    )
    assert out.confidence == "medium"
    assert out.sources == []
    assert out.extra["c"] == ""

    out = finalize(
        GeneratedAnswer(
            ans_status="answerable",
            confidence="high",
            answer="Yes, we enforce MFA.",
            sources=["[id=OID-1]", "id=c1", "c1"],
        ),
        hits,
        [_portal_target()],
    )
    assert out.sources == ["OID-1"]
    text = portal_text(out)
    assert text.startswith("Yes, we enforce MFA.")
    assert "answerable" in text and "OID-1" in text
    prompt = build_user(Question(id="q", text="MFA?"), hits)
    assert "OID-1" in prompt and "c1" not in prompt
    assert _enum_misses(raw, [enum_t, comment, appl, evidence]) == []
    bad = M(
        ans_status="answerable",
        confidence="high",
        answer="yeah we do",
        sources=["OID-1"],
        c="ok",
        ni_applicable="maybe",
        ni_evidence="x",
    )
    misses = _enum_misses(bad, [enum_t, comment, appl, evidence])
    assert [m[0] for m in misses] == ["answer", "ni_applicable"]
    print("finalize ok")


if __name__ == "__main__":
    from pipeline.config.config import load_settings
    from pipeline.llm.ollama import OllamaClient
    from pipeline.config.logging_setup import setup_logging
    from pipeline.rag.index import build_retriever

    p = argparse.ArgumentParser()
    p.add_argument("question", nargs="*")
    p.add_argument("--show-prompt", action="store_true")
    p.add_argument("--check-finalize", action="store_true")
    a = p.parse_args()
    if a.check_finalize:
        _check_finalize()
        if not a.question:
            raise SystemExit(0)
    if not a.question:
        p.error("question required")

    setup_logging()
    s = load_settings()
    retriever = build_retriever(s)
    try:
        gen = Generator(OllamaClient(s), retriever)
        targets = [_portal_target()]
        for i, qtext in enumerate(a.question):
            if i:
                print()
            ans, hits, system, user, dump = gen.answer(Question(id=f"cli-{i+1}", text=qtext), targets)
            print("Q:", qtext)
            for h in hits:
                print(f"hit {h.score:.3f} {h.chunk.id} {h.chunk.original_id}")
            if a.show_prompt:
                print("--- system ---")
                print(system)
                print("--- user ---")
                print(user)
            print(json.dumps(dump, indent=2))
            print("portal:", portal_text(ans))
    finally:
        retriever.close()
