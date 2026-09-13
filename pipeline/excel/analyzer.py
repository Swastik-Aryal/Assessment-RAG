import argparse
import json
import logging
from pathlib import Path

from pipeline.excel.render import render
from pipeline.excel.schema import WorkbookAnalysis, WorkbookSchema
from pipeline.llm.structured import call_structured

log = logging.getLogger(__name__)

SYSTEM = """You describe the layout of a messy security-questionnaire Excel workbook.
The rendering lists every non-empty cell, merged ranges, dropdowns, and per-column stats.
Do not assume a fixed header row or column. Layouts vary: banners, grey section bands, inconsistent headers, stacked or side-by-side tables.

needs_full_render: set true only if this rendering is truncated or hides rows/cells you need to locate every question table (compact mode cuts cells and may stop after 50 rows). more_info_reason says what is missing. Still fill sheets as far as you can.
If you can see the layout, needs_full_render=false and more_info_reason="".
If the user message starts with FULL RENDER, every row is present: needs_full_render must be false.

Identify:
- cover / instructions / response-key sheets (tables empty)
- every question table: header_row, first_data_row, last_data_row or null, first_col, last_col as letters
- question_col: the column whose cells are actually questions (end with ? or long text). col_stats.pct_question_like is evidence. Never pick Domain/Category/Section label columns.
- id_col if a column is mostly short ids like 1.1.1; id_regex only if you are sure, else null
- section_label_col if empty question rows carry a section banner; null if unsure
- fill_targets: every mostly-empty column a human respondent would type into. No extra instruction
  on the sheet is required. Exactly one role=answer. Other fillable columns (applicability, comments,
  extra closed fields) are their own targets. strategy=llm unless a knowledge-base system cannot know
  the value (owner/assignee names, evidence attachment refs, dates a person must enter).
  answer.required=true.

For each fill_target you decide format, allowed_values, and rule. Evidence can conflict: the header
wording, this column's dropdown, the workbook response key, and neighboring columns may not agree
(a dropdown is sometimes copied onto the wrong column). Choose the vocabulary that matches THIS
column's meaning, not whichever list appears first. Write that choice into `rule` so the answering
model follows it. If nothing implies a closed set, use free_text and empty allowed_values. Do not
invent values with no support in the header, dropdown, or key.

- instructions_text: combine How to complete / instruction sheets (applies to all tables)
- response_key: value/meaning pairs from a key sheet or an in-sheet legend

Columns are letters (A, B, C). table_id must be unique. Sheets keep their real names.

Column names may contain rules/format instructions. These may override the overall format/rule.
"""


def analyze(gemini, rendering: str, full: bool = False) -> WorkbookAnalysis:
    """Ask Gemini for layout. `full` means the rendering already has every row."""
    user = rendering
    if full:
        user = "FULL RENDER — every row and cell is below. needs_full_render must be false.\n\n" + user
    return call_structured(gemini, SYSTEM, user, WorkbookAnalysis)


def _schema(analysis: WorkbookAnalysis) -> WorkbookSchema:
    return WorkbookSchema.model_validate(
        analysis.model_dump(exclude={"needs_full_render", "more_info_reason"})
    )


def infer_schema(path, gemini, out_dir: Path | None = None, force_full: bool = False) -> WorkbookSchema:
    """Compact render then LLM. If the model asks, retry with a full render. Writes schema.json."""
    attempts = []
    rendering = render(path, full=force_full)
    analysis = analyze(gemini, rendering, full=force_full)
    attempts.append({"full": force_full, **analysis.model_dump()})
    if analysis.needs_full_render and not force_full:
        log.info("llm asked for full render: %s", analysis.more_info_reason or "(no reason)")
        rendering = render(path, full=True)
        analysis = analyze(gemini, rendering, full=True)
        attempts.append({"full": True, **analysis.model_dump()})
    schema = _schema(analysis)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "schema.json").write_text(
            json.dumps({"attempts": attempts, "schema": schema.model_dump()}, indent=2),
            encoding="utf-8",
        )
    return schema


if __name__ == "__main__":
    from pipeline.config import load_settings
    from pipeline.llm.gemini import GeminiClient
    from pipeline.logging_setup import setup_logging

    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--force-full", action="store_true")
    a = p.parse_args()
    setup_logging()
    s = load_settings()
    out = s.OUTPUT_DIR / "excel"
    schema = infer_schema(a.path, GeminiClient(s), out_dir=out, force_full=a.force_full)
    print(schema.model_dump_json(indent=2))
    print("wrote", out / "schema.json")
