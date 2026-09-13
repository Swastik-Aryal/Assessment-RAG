# Mock services

Two local HTTP servers stand in for a real email provider and a real vendor
portal. No internet, no accounts, no credentials. **Do not modify these files** —
we grade your pipeline against an identical copy.

## Run

Install the mock-system dependencies once, then start both servers from the
`assessment/` root in two terminals:

```bash
pip install -r requirements.txt

# terminal 1 — mock mailbox on http://127.0.0.1:8025
python -m uvicorn mocks.mail_server:app --host 127.0.0.1 --port 8025

# terminal 2 — mock portal on http://127.0.0.1:8080
python -m uvicorn mocks.portal_server:app --host 127.0.0.1 --port 8080
```

The mailbox exposes an HTTP API (docs at `http://127.0.0.1:8025/docs`). The
portal is a **website, not an API** — open `http://127.0.0.1:8080/` in a browser
to see the login page.

Pick which questionnaire scenario to run by resetting the mailbox:

```bash
curl -X POST "http://127.0.0.1:8025/admin/reset?scenario=excel"    # Type A
curl -X POST "http://127.0.0.1:8025/admin/reset?scenario=portal"   # Type B
```

A reset wipes the mailbox and seeds the appropriate incoming client email. Do
this at the start of every run.

---

## Fixed addresses

| Role | Address |
|---|---|
| Your pipeline (send as / read from) | `pipeline@securitypal.test` |
| Client (sends the questionnaire, expects it back) | `vendor-assessments@acme-client.test` |
| Portal support desk (issues hourly password) | `access@portal.acme-client.test` |
| Portal username | `securitypal` |

---

## Mail server API — `http://127.0.0.1:8025`

| Method & path | Purpose |
|---|---|
| `GET /inbox` | List messages in your inbox (summaries with `snippet`). |
| `GET /inbox/{id}` | Full message including base64 `attachments`. |
| `GET /inbox/{id}/attachments/{filename}` | Raw attachment bytes. |
| `POST /send` | Send an email. Body: `{from, to, subject, body, attachments:[{filename, content_b64}]}` (`from` defaults to the pipeline address). |
| `GET /sent` | Everything your pipeline has sent (debugging). |
| `POST /admin/reset?scenario=excel\|portal` | Wipe + seed a scenario. |
| `GET /admin/status` | `{scenario, acknowledged, delivered_back, ...}` — how the grader checks a run. |

**Counterparty behaviour** (automatic reactions to mail you send):

- Email to `access@portal.acme-client.test` (or subject containing `PORTAL
  ACCESS`) → a reply lands in your inbox containing the **current** password.
- Email to the client with an **attachment** (excel scenario) → marks the run
  `delivered_back` and replies confirming receipt.
- Any other email to the client → marks the run `acknowledged`.

## Portal — a web app at `http://127.0.0.1:8080`

The portal is a **website, not an API** — there is no JSON endpoint and no
`/docs`. Your pipeline drives it the way a person would: it loads the login
page, signs in, reads the questions off the questionnaire page, types an answer
into each field, and submits the form. Use a browser-automation tool
(Playwright, Selenium, …) or any HTTP client that follows cookies and posts HTML
forms.

| Page | What it does |
|---|---|
| `GET /` | Login page — a form with `username` and `password` fields. |
| `POST /login` | Validates the current hourly password. On success, sets a `sp_session` cookie and **redirects (303) to `/questionnaire`**. On failure, re-renders the login page with an error (`401`). |
| `GET /questionnaire` | The full questionnaire as an HTML form: one `<textarea name="answer_<id>">` per question (question ids are `Q1`…`Q26`). Requires the session cookie; otherwise redirects to the login page. |
| `POST /questionnaire` | Submits the form. If any field is blank it re-renders with an error (`400`); when all are filled it records the submission and shows a confirmation page. |

The password rotates on the hour, and a session is valid only within the hour it
was created. If it rotates mid-session, `/questionnaire` bounces you back to the
login page (`/?expired=1`) and you must request a fresh password by email and log
in again.

---

## Minimal walkthroughs

**Type A (excel):**
```python
import requests, base64
M = "http://127.0.0.1:8025"
requests.post(f"{M}/admin/reset", params={"scenario": "excel"})
msg = requests.get(f"{M}/inbox").json()[0]
full = requests.get(f"{M}/inbox/{msg['id']}").json()
xlsx_bytes = base64.b64decode(full["attachments"][0]["content_b64"])
# ... acknowledge, parse xlsx, answer via RAG, write answers into a workbook ...
requests.post(f"{M}/send", json={
    "to": "vendor-assessments@acme-client.test",
    "subject": "Completed Security Questionnaire",
    "body": "Please find the completed questionnaire attached.",
    "attachments": [{"filename": "completed.xlsx", "content_b64": "<base64>"}],
})
```

**Type B (portal)** — driven like a browser (Playwright shown; Selenium or a
cookie-aware HTTP client work too):
```python
import re, requests
from playwright.sync_api import sync_playwright

M, P = "http://127.0.0.1:8025", "http://127.0.0.1:8080"
requests.post(f"{M}/admin/reset", params={"scenario": "portal"})
# read the client email (it names the portal + support desk), acknowledge, then
# request and read the current password:
requests.post(f"{M}/send", json={"to": "access@portal.acme-client.test",
                                  "subject": "PORTAL ACCESS REQUEST", "body": "Requesting access."})
reply = requests.get(f"{M}/inbox/{requests.get(f'{M}/inbox').json()[-1]['id']}").json()
pw = re.search(r"password is:\s*([A-Z0-9]+)", reply["body"]).group(1)

with sync_playwright() as pw_ctx:
    page = pw_ctx.chromium.launch().new_page()
    page.goto(P)                                  # login page
    page.fill("#username", "securitypal")
    page.fill("#password", pw)
    page.click("button[type=submit]")             # -> /questionnaire
    for label in page.locator(".q label").all_inner_texts():
        pass                                      # read each question, answer via RAG
    page.fill("textarea[name=answer_Q1]", "Yes, we comply.")
    # ... fill every answer_<id> ...
    page.click("button[type=submit]")             # submit; confirmation page renders
```
