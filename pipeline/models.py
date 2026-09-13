from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class Scenario:
    """How classify() labels an inbound client mail."""
    EXCEL = "EXCEL"
    PORTAL = "PORTAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class Question:
    """One extracted questionnaire item (Excel row or portal field)."""
    id: str
    text: str
    sheet: str = ""
    row: int = 0
    section_label: str = ""
    table_id: str = ""


class FieldFormat(BaseModel):
    """How a fillable cell is constrained: enum dropdown or free text."""
    type: Literal["enum", "free_text"]
    allowed_values: list[str] = Field(default_factory=list)
    max_length: int | None = None


class FillTarget(BaseModel):
    """One column/field the pipeline may write. strategy skip leaves it untouched."""
    target_id: str
    col: str = ""
    header: str = ""
    role: Literal["answer", "comment", "applicability", "evidence", "owner", "other"]
    strategy: Literal["llm", "constant", "skip"] = "llm"
    constant_value: str | None = None
    format: FieldFormat
    rule: str = ""
    required: bool = False


class GeneratedAnswer(BaseModel):
    """Core model fields plus extra Excel columns keyed by fill_target target_id."""
    ans_status: Literal["answerable", "needs_review", "unanswerable"]
    confidence: Literal["high", "medium", "low"]
    answer: str
    sources: list[str]
    extra: dict[str, str] = Field(default_factory=dict)
