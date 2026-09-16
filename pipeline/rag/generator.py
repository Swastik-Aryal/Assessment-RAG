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
- ans_status: answerable when a the retrieved context directly answers the question; needs_review when the retrived context only partly answers the question, or any part of a multi-part question is unsupported; unanswerable when the context doesnot the question.
- confidence: high only for a near-paraphrase match, and never unless ans_status=answerable. Otherwise medium or low based on the retrieval and/or your ability.
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


if __name__ == "__main__":
    from pathlib import Path
    from pipeline.rag.kb import Chunk

    filler_hits = [
        Hit(Chunk("uuid-aaa", "Section Heading: Access Control\nQuestion Text: Do you encrypt data at rest?\nAnswer: Yes, AES-256.", "3.1", "Access Control", "Encryption"), 0.92),
        Hit(Chunk("uuid-bbb", "Section Heading: Data Protection\nQuestion Text: What is your data retention policy?\nAnswer: 7 years.", "4.2", "Data Protection", "Retention"), 0.78),
        Hit(Chunk("uuid-ccc", "Section Heading: Access Control\nQuestion Text: Do you use MFA?\nAnswer: Yes, for all employees.", "3.4", "Access Control", "Authentication"), 0.65),
    ]

    excel_targets = [
        FillTarget(target_id="response", col="E", header="Vendor Response", role="answer",
                   format=FieldFormat(type="enum", allowed_values=["Yes", "No", "N/A", "Partial"]),
                   rule="Pick the value that best matches your answer."),
        FillTarget(target_id="comment", col="F", header="Additional Comments", role="comment",
                   format=FieldFormat(type="free_text"),
                   rule="Explain your answer and cite KB rows."),
        FillTarget(target_id="evidence", col="G", header="Evidence / Reference", role="evidence",
                   format=FieldFormat(type="free_text")),
        FillTarget(target_id="owner", col="H", header="Control Owner", role="owner",
                   strategy="constant", constant_value="Security Team",
                   format=FieldFormat(type="free_text")),
        FillTarget(target_id="applicability", col="I", header="Applicability", role="applicability",
                   format=FieldFormat(type="enum", allowed_values=["Applicable", "Not Applicable"]),
                   rule="Mark Applicable unless the control is completely irrelevant."),
    ]

    portal_targets = [_portal_target()]

    excel_q = Question(id="SEC-3.1", text="Does your organization encrypt all data at rest using AES-256 or equivalent?",
                       sheet="Security Controls", row=15, section_label="3. Access Control", table_id="table_1")
    portal_q = Question(id="Q12", text="Describe your organization's incident response process.")

    scenarios = {}
    for name, targets, q in [("excel", excel_targets, excel_q), ("portal", portal_targets, portal_q)]:
        system = build_system(targets, instructions="Complete all required fields." if name == "excel" else "")
        user = build_user(q, filler_hits)
        model_cls = llm_answer_model(targets)
        schema = model_cls.model_json_schema()
        scenarios[name] = {
            "question": {"id": q.id, "text": q.text, "sheet": q.sheet, "row": q.row, "table_id": q.table_id},
            "targets": [t.model_dump() for t in targets],
            "system_prompt": system,
            "user_prompt": user,
            "expected_json_schema": schema,
        }

    out = Path("outputs/prompt_visualization.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scenarios, indent=2, ensure_ascii=False))

    for name, data in scenarios.items():
        print(f"\n{'='*60}")
        print(f"  {name.upper()} SCENARIO")
        print(f"{'='*60}")
        print(f"\nSYSTEM PROMPT:\n{data['system_prompt']}")
        print(f"\nUSER PROMPT:\n{data['user_prompt']}")
        print(f"\nEXPECTED JSON SCHEMA KEYS: {list(data['expected_json_schema']['properties'].keys())}")
    print(f"\nFull details -> {out}")

