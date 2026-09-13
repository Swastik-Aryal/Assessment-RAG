import logging
import re
from collections import defaultdict

from openpyxl.utils import column_index_from_string

from pipeline.excel.render import cell_str
from pipeline.excel.schema import TableSchema, WorkbookSchema
from pipeline.models import Question

log = logging.getLogger(__name__)
BUILTIN_ID = re.compile(r"^[A-Za-z]{0,8}\d+(?:\.\d+)*$")


def col_idx(letter: str) -> int:
    """Excel column letter to 1-based index (A=1)."""
    return column_index_from_string(letter)


def table_last_row(ws, table: TableSchema) -> int:
    """Last data row: schema value, or calculate it based on the last non-empty row in the table span."""
    if table.last_data_row:
        return table.last_data_row
    c0, c1 = col_idx(table.first_col), col_idx(table.last_col)
    last = table.first_data_row
    for r in range(table.first_data_row, (ws.max_row or 1) + 1):
        if any(cell_str(ws.cell(r, c).value) for c in range(c0, c1 + 1)):
            last = r
    return last


def id_pattern(table: TableSchema):
    """Compiled id regex from the schema, or the built-in 1.1.1-style pattern."""
    if table.id_regex:
        return re.compile(table.id_regex)
    return BUILTIN_ID


def extract_table(ws, table: TableSchema) -> list[Question]:
    """Pull questions from one table. Section banners update metadata only, never become questions."""
    qcol = col_idx(table.question_col)
    header_q = cell_str(ws.cell(table.header_row, qcol).value)
    last = table_last_row(ws, table)
    idcol = col_idx(table.id_col) if table.id_col else None
    seccol = col_idx(table.section_label_col) if table.section_label_col else None
    pat = None
    if table.id_col:
        try:
           pat = id_pattern(table)
        except re.error:
            return []
    section = ""
    if seccol:
        for r in range(table.header_row + 1, table.first_data_row):
            lab = cell_str(ws.cell(r, seccol).value)
            if lab:
                section = lab
    out = []
    for r in range(table.first_data_row, last + 1):
        q = cell_str(ws.cell(r, qcol).value)
        if not q:
            if seccol:
                lab = cell_str(ws.cell(r, seccol).value)
                if lab:
                    section = lab
            continue
        if q == header_q:
            continue
        rid = cell_str(ws.cell(r, idcol).value) if idcol else ""
        if table.id_col and (not rid or not pat.fullmatch(rid)):
            continue
        # blank id -> sheet!row; collisions get table_id prefixed below
        qid = rid or f"{ws.title}!{r}"
        out.append(
            Question(
                id=qid,
                text=q,
                sheet=ws.title,
                row=r,
                section_label=section,
                table_id=table.table_id,
            )
        )
    seen: dict[str, int] = defaultdict(int)
    for q in out:
        seen[q.id] += 1
    if any(n > 1 for n in seen.values()):
        for q in out:
            if seen[q.id] > 1:
                q.id = f"{table.table_id}:{q.id}"
    return out


def extract(wb, schema: WorkbookSchema) -> list[Question]:
    """Extract questions from every table in the schema."""
    questions = []
    for sheet in schema.sheets:
        if sheet.sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet.sheet_name]
        for table in sheet.tables:
            questions.extend(extract_table(ws, table))
    return questions


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    from openpyxl import load_workbook

    from pipeline.config import load_settings
    from pipeline.excel.schema import WorkbookSchema
    from pipeline.logging_setup import setup_logging
    from pipeline.llm.gemini import GeminiClient
    from pipeline.excel.analyzer import infer_schema

    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--force-full", action="store_true")
    a = p.parse_args()
    setup_logging()
    s = load_settings()
    out = s.OUTPUT_DIR / "excel"
    schema = infer_schema(a.path, GeminiClient(s), out_dir=out, force_full=a.force_full)
    wb = load_workbook(a.path, data_only=False)
    try:
        qs = extract(wb, schema)
    finally:
        wb.close()
    by = defaultdict(int)
    for q in qs:
        by[q.sheet] += 1
        print(f"{q.sheet}\t{q.row}\t{q.id}\t{q.section_label[:40]}\t{q.text[:80]}")
    
    with open(out / "questions.json", "w", encoding="utf-8") as f:
        json.dump([q.__dict__ for q in qs], f, indent=2)
    print(json.dumps({"n": len(qs), "by_sheet": by, "ids_unique": len({q.id for q in qs}) == len(qs)}))
