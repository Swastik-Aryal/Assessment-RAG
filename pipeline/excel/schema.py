"""Pydantic schemas describing workbook layout: tables, sheets, fill targets, response keys."""
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.models import FillTarget


class TableSchema(BaseModel):
    """One question table: header, data range, question/id columns, and cells we fill."""
    table_id: str
    header_row: int | None = None
    first_data_row: int
    last_data_row: int | None = None
    first_col: str
    last_col: str
    question_col: str
    id_col: str | None = None
    section_label_col: str | None = None
    fill_targets: list[FillTarget]


class SheetSchema(BaseModel):
    """One sheet. Non-question sheets keep tables empty."""
    sheet_name: str
    role: Literal["questions", "instructions", "response_key", "cover", "other"]
    tables: list[TableSchema] = Field(default_factory=list)


class ResponseKeyEntry(BaseModel):
    """One allowed response value and its meaning from a key/legend sheet."""
    value: str
    meaning: str = ""


class WorkbookSchema(BaseModel):
    """LLM description of the whole workbook. Gemini-compatible (flat, no dict[str, X])."""
    instructions_text: str = ""
    response_key: list[ResponseKeyEntry] = Field(default_factory=list)
    sheets: list[SheetSchema]


class WorkbookAnalysis(WorkbookSchema):
    """Analyzer output. needs_full_render asks for an untruncated workbook render."""
    needs_full_render: bool = False
    more_info_reason: str = ""
