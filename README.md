# Assessment-RAG Pipeline

Autonomous, email-triggered pipeline that receives security questionnaires (Excel or web portal), answers them from a Knowledge Base via RAG, and delivers the completed questionnaire back with no human in the loop.


---

## Quick start

### Prerequisites

- Ollama running locally with a model pulled. Download from [Ollama website](https://ollama.com).
- A Gemini API key (free tier works)

### 1. Clone and create venv

```bash
git clone "https://github.com/Swastik-Aryal/Assessment-RAG" && cd Assessment-RAG
python -m venv .venv

# Windows
.\.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate
```

### 2. Install dependencies

```bash
# For linux/windows users if you wish to run the embedding model on GPU:

# See https://pytorch.org/get-started/locally/ for the appropriate command
pip install torch torchvision --index-url https://download.pytorch.org/whl/cuXXX
```

```bash
pip install -r requirements.txt -r requirements-pipeline.txt
python -m playwright install chromium
```

`requirements.txt` is for the mock servers. `requirements-pipeline.txt` is for the pipeline itself.


### 3. Pull and serve the Ollama model

```bash
ollama pull qwen3.5:4b
ollama serve
```

Any Ollama model works -- set `OLLAMA_MODEL` in `.env` to change it.

### 4. Configure `.env`

```bash
cp .env.example .env
```

Then set `GEMINI_API_KEY`. The defaults work out of the box for everything else. Key settings you might want to change:

| Variable | Default | Notes |
|----------|---------|-------|
| `GEMINI_API_KEY` | (required) | Your Google AI Studio key |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Any Gemini model that supports structured output |
| `GEMINI_MIN_INTERVAL_SECONDS` | `0` | Set to `6` for free-tier 10 RPM pacing |
| `OLLAMA_MODEL` | `qwen3.5:4b` | Any Ollama model name |
| `RETRIEVER` | `dense` | `dense`, `bm25`, or `hybrid` |
| `DENSE_WEIGHT` | `0.7` | RRF weight split (BM25 gets `1 - DENSE_WEIGHT`) |
| `TOP_K` | `5` | Chunks fed to the LLM per question |
| `EMBED_DEVICE` | `cpu` | Set to `cuda` if you have a GPU |
| `PORTAL_HEADLESS` | `true` | `false` to watch the browser fill the portal |
| `PORTAL_SLOW_MO_MS` | `0` | Add delay (ms) to browser actions for demo |
| `LOG_LEVEL` | `INFO` | `DEBUG` prints per-question lines and empty polls |

### 5. Start the mock servers

In two separate terminals (from the repo root):

```bash
# Terminal 1 -- mail server
python -m uvicorn mocks.mail_server:app --host 127.0.0.1 --port 8025

# Terminal 2 -- portal server
python -m uvicorn mocks.portal_server:app --host 127.0.0.1 --port 8080
```

### 6. Run the pipeline

Pick a scenario, reset the mailbox, and start:

```bash
python -m pipeline
```

The pipeline polls the inbox, acknowledges the request, processes it, and sends the completed questionnaire back. `Ctrl+C` to stop.

Check the result:
```bash
curl http://127.0.0.1:8025/admin/status
```

---

## How it works

The pipeline is a single-process poll loop that watches the mock mailbox. When a new email arrives from the client:

1. **Classify** : `.xlsx`/`.xlsm` attachment means Excel, a URL in the body means portal, anything else is skipped.

2. **Acknowledge** : before any processing, reply with `Re: <subject>` containing a `[ref:<msg_id>]` marker. All state is derived from the mailbox using these markers: "acknowledged" if `/sent` has a mail with the ref, "completed" if that mail's subject starts with `Completed:`. No database or local state file. Mid-run arrivals are also acknowledged via the `ack_pending` callback at ~10% progress intervals.

3. **Ingest** : Excel: render workbook to text, Gemini infers the sheet/table schema (with validation retries), then extract questions. Portal: email the access desk for the current password, log in via Playwright, parse questions from HTML, close the browser.

4. **Answer** : for each question, retrieve top-k KB chunks, generate a structured JSON answer via Ollama (status, confidence, answer, citations, plus extra workbook columns), validate citations and canonicalize enum values.

5. **Deliver** -- Excel: write answers into a copy of the original workbook, append a summary sheet, email back as `Completed: <subject>` with the file attached. Portal: open a new browser, login with a fresh password, submit the form, send `Completed: <subject>` (no attachment). Before sending, the pipeline checks `ack_alive` -- if a mailbox reset wiped the acknowledgement mid-run, delivery is skipped. Failed runs go to `failure.json` and are not retried in the same session.

> ![IMPORTANT]
> - It is recommended not to `/reset` the mailbox during the processing of a mail and before the delivered mail is sent.
> - If you do so, the `ack_alive` flag will be set to `false` and the current process will complete but the final **completed** email will not be sent to the client. This is an intended action.
> - The previous email will be sent to `failed`.
> - The watcher will keep running and will acknowledge and work on the new state's mails.


---

## Architecture

For detailed, check here.

### RAG

- **Chunking:** 1 KB row = 1 chunk (117 total). Each chunk concatenates Section Heading, Control Heading, Question Text, Answer, and Notes/Comment into a single text block. Metadata (`original_id`, `section`, `control`) is stored alongside for citations and eval.
- **Embedding:** `jina-embeddings-v5-text-nano-retrieval` (212M params, 768-d, 19th on MTEB English retrieval). Separate `query` and `document` prompt prefixes. Chose for strong recall on this KB size while keeping inference fast even with a loaded LLM on GPU.
- **Retrieval:** Dense (Qdrant local, cosine), BM25 (in-memory `bm25s`), or Hybrid (top-20 from each, fused via weighted RRF with k=60). Qdrant collection is cached and reused across runs.
- **Generation:** `qwen3.5:4b` via Ollama. Structured JSON output per question: `ans_status` (answerable/needs_review/unanswerable), `confidence` (high/medium/low), `answer`, `sources`, plus extra per-table columns. `finalize` drops hallucinated citations, canonicalizes enum values, and coerces confidence down if status is not answerable.

### Excel flow

1. **Render** workbook to text (cell values, merges, dropdowns, fill colors, column stats). Compact first (50 rows), full on demand.
2. **Gemini structured call** infers the schema: sheet roles, table boundaries, question/id/section columns, fill targets with format (enum or free text), allowed values, and rules. Auto-retries with full render if truncated.
3. **Validate** sheet names and header cells against the workbook. Up to 3 retries with error feedback to Gemini.
4. **Extract** questions per table with `stop_before` boundaries to prevent bleed across stacked tables. Keys are `sheet!table_id!row`.
5. **Answer** each question via RAG. `strategy=llm` columns are filled by the model, `strategy=constant` writes a fixed value, `strategy=skip` is left untouched.
6. **Write** into a byte-copy of the original (preserving images/formatting). Append a `SecurityPal Summary` sheet.

### Portal flow

1. Email the access desk for the current password, extract via regex. Fresh password on every login (never cached).
2. Playwright login with up to 3 retries on bad credentials.
3. Parse questions from HTML, close the browser. RAG generation happens offline.
4. **Rotation guard:** if < 30 seconds until the next epoch-hour password rotation, sleep past the boundary before submitting.
5. **Submit loop** (max 3): new browser, fresh password, login, fill textareas, submit. Handles `incomplete` (auto-fill blanks with "Needs review."), `expired` (new session + retry), and `submitted` (screenshot + break).
6. `ack_alive` guard before each login and after submission.

---

## Project structure

```
pipeline/
  __main__.py          Watch loop entry point
  models.py            Scenario, Question, FillTarget, GeneratedAnswer
  flows.py             run_excel, run_portal orchestration
  config/              Settings from .env (pydantic-settings)
  llm/                 Gemini + Ollama clients, structured output helper
  mail/                Mail client + inbox watcher
  rag/                 KB loader, embedder, retriever (dense/BM25/hybrid), generator
  excel/               Render, schema analysis, question extraction, workbook writer
  portal/              Playwright browser automation, HTML parser
rag_evals/             Retrieval evaluation framework
data/                  Knowledge base + questionnaire files (provided)
mocks/                 Mock mail + portal servers (do not modify)
```

---

## Outputs and logs

All run artifacts go under `outputs/`. Each pipeline session creates a timestamped folder:

```
outputs/
  20260916T120000Z/              Session folder
    runs.log                     Session-level log
    20260916T120005Z_excel_msg-0001/   Per-request folder
      run.log                    Detailed log (DEBUG level)
      request.json               Original email metadata
      schema.json                Gemini schema inference attempts + final schema
      questions.json             Extracted questions
      answers.json               Raw LLM output per question
      retrieval.json             Retrieved chunks per question
      prompts.json               System + user prompts per question
      Completed - <name>.xlsx    The filled workbook (Excel scenario)
      submitted.png              Portal submission screenshot (portal scenario)
```

**Cache:** `cache/qdrant/` stores the local Qdrant vector index. It is reused across runs if the KB and embedding dimensions have not changed. Both `outputs/` and `cache/` are gitignored.

---

## Component testing

Most modules have `if __name__ == "__main__"` entry points for standalone testing. These are useful for debugging individual components without running the full pipeline.


### Mail / watcher

```bash
python -m pipeline.mail.client --status   # Mailbox status
python -m pipeline.mail.client --inbox    # List inbox
python -m pipeline.mail.client --sent     # List sent
python -m pipeline.mail.client --reset excel   # Reset to Excel scenario
python -m pipeline.mail.watcher           # Show requests + their state
```

### Knowledge base and retrieval

```bash
python -m pipeline.rag.kb                 # Dump 117 chunks to outputs/kb_chunks.json
python -m pipeline.rag.index "Do you encrypt data at rest?"   # Test retrieval
```

### Excel components

```bash
python -m pipeline.excel.render <path.xlsx>              # Text render (compact)
python -m pipeline.excel.render <path.xlsx> --full       # Full render
python -m pipeline.excel.analyzer <path.xlsx>            # Gemini schema inference
python -m pipeline.excel.analyzer <path.xlsx> --force-full
python -m pipeline.excel.extractor <path.xlsx>           # Schema + question extraction
python -m pipeline.excel.writer test_excels/             # Full RAG over a folder of workbooks
```

### Portal

```bash
python -m pipeline.portal.browser --password <pw>        # Login, read, submit test
python -m pipeline.portal.browser --password <pw> --slow 500  # Slow for watching
```

### Generator / LLM

```bash
python -m pipeline.rag.generator                         # Prompt visualization (no LLM call)
python -m pipeline.llm.structured                        # Gemini structured output smoke test
```

---

## Retrieval evaluation

The `rag_evals/` package measures retrieval quality (not answer quality).

### Pre-generated data

- `rag_evals/original.json` -- 117 queries (verbatim KB questions)
- `rag_evals/synthetic.json` -- 234 queries (2 synthetic rephrasings per KB row, generated via `qwen3.5:4b`)
- `rag_evals/evals/` -- saved eval results

### Run evals

```bash
# On original KB questions
python -m rag_evals --data rag_evals/original.json

# On synthetic rephrasings
python -m rag_evals --data rag_evals/synthetic.json

# Custom k
python -m rag_evals --data rag_evals/synthetic.json --k 20
```

Output goes to `rag_evals/evals/eval_results.json` by default (or `--out <dir>`).

### Regenerate eval data

```bash
# Extract original questions from KB
python -m rag_evals.generate_data --original

# Generate synthetic rephrasings (requires Ollama)
python -m rag_evals.generate_data --synthetic          # 2 per question (default)
python -m rag_evals.generate_data --synthetic --k 3    # 3 per question
```

### Results

**Original (117 queries):**

| Metric | Dense | BM25 | Hybrid (0.8/0.2) | Hybrid (0.5/0.5) |
|--------|-------|------|-------------------|-------------------|
| Recall@1 | 0.983 | 0.983 | 0.983 | **0.992** |
| Recall@5 | 1.000 | 1.000 | 1.000 | 1.000 |
| Recall@10 | 1.000 | 1.000 | 1.000 | 1.000 |
| MRR@5 | 0.992 | 0.992 | 0.992 | **0.996** |

**Synthetic (234 queries):**

| Metric | Dense | BM25 | Hybrid (0.8/0.2) | Hybrid (0.5/0.5) |
|--------|-------|------|-------------------|-------------------|
| Recall@1 | **0.932** | 0.594 | 0.825 | 0.756 |
| Recall@5 | **0.992** | 0.812 | 0.927 | 0.919 |
| Recall@10 | **1.000** | 0.876 | 0.987 | 0.983 |
| MRR@5 | **0.961** | 0.677 | 0.872 | 0.826 |

Dense retrieval dominates on synthetic rephrasings where BM25 keyword overlap drops. On original (verbatim) queries all modes perform near-perfectly.

---

## Switching scenarios

The mock mailbox supports one scenario at a time. To switch:

```bash
curl -X POST "http://127.0.0.1:8025/admin/reset?scenario=portal"
```

This wipes the mailbox and seeds the new scenario's email. The pipeline detects the change via its `ack_alive` guard -- if a run was in progress, it will finish processing but will not send a Completed email (since the acknowledgement was wiped).

You can restart the pipeline after resetting, or let it pick up the new request on the next poll.
