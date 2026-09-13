"""Mock vendor portal — a real web app the candidate's pipeline must drive.

There is NO JSON API. The pipeline logs in through the login page, reads the
whole questionnaire from the questionnaire page, types an answer into each
question, and submits the form — exactly as a person would in a browser.

Pages:
    GET  /                -> login page (form: username, password)
    POST /login           -> validates the current hourly password, sets a
                             session cookie, redirects to /questionnaire
    GET  /questionnaire   -> the full questionnaire as an HTML form
                             (one textarea per question, named answer_<id>)
    POST /questionnaire   -> submit all answers; re-renders with an error if any
                             are blank; shows a confirmation page when complete

The password rotates every hour (see mocks/shared.current_password) and must be
obtained over email. A session is valid only within the hour it was created — if
the password rotates mid-session, the pipeline is bounced back to the login page.

Drive this with a browser-automation tool (Playwright, Selenium, …) or any HTTP
client that follows cookies and posts HTML forms.

Run:
    uvicorn mocks.portal_server:app --host 127.0.0.1 --port 8080
"""
from __future__ import annotations

import html
import json
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from mocks.shared import PORTAL_USERNAME, current_password

QUESTIONS_JSON = Path(__file__).resolve().parent / "portal_questions.json"

# No OpenAPI docs — this is a web portal, not an API.
app = FastAPI(title="SecurityPal Vendor Security Portal", docs_url=None, redoc_url=None)

QUESTIONS = json.loads(QUESTIONS_JSON.read_text())
QUESTION_IDS = [q["id"] for q in QUESTIONS]

# session id -> {"hour": int, "answers": {id: text}, "submitted": bool}
SESSIONS: dict[str, dict] = {}


def _hour() -> int:
    return int(time.time() // 3600)


def _get_session(request: Request):
    sid = request.cookies.get("sp_session")
    s = SESSIONS.get(sid or "")
    if s and s["hour"] == _hour():
        return sid, s
    return sid, None


# --------------------------------------------------------------------------- #
# Presentation                                                                #
# --------------------------------------------------------------------------- #
LOGOMARK = (
    '<svg width="24" height="20" viewBox="0 0 146 126" fill="none" '
    'xmlns="http://www.w3.org/2000/svg"><path d="M141.725 0.35498C141.715 22.205 '
    "124.015 39.895 102.175 39.895C125.855 39.895 145.045 59.0951 145.045 "
    "82.7651C145.045 83.8851 145.005 84.9953 144.915 86.0952C143.215 108.215 "
    "124.735 125.645 102.175 125.645H4.28516C4.28516 104.085 21.5151 86.575 "
    "42.9551 86.105L42.7246 86.0952C19.5548 85.505 0.955127 66.5349 0.955078 "
    "43.2251C0.955078 42.1051 0.994963 40.995 1.08496 39.895C2.75495 18.1352 "
    "20.6748 0.915207 42.7246 0.35498H141.725ZM95.0752 40.0347C77.7157 44.555 "
    "67.3146 44.555 49.9551 40.0347C54.4754 57.3941 54.4754 67.7955 49.9551 "
    "85.1548C67.3145 80.6344 77.7157 80.6344 95.0752 85.1548C90.5549 67.7955 "
    '90.5548 57.3941 95.0752 40.0347Z" fill="#1F1F1F"></path></svg>'
)

STYLE = """<style>
  :root { --ink:#1F1F1F; --muted:#5C5C5C; --line:#D9D9D9; --line2:#EBEBEB;
    --canvas:#FCFCFC; --card:#FFFFFF; --blue:#015CE6; --blue2:#1A6CE9;
    --tint:#E6F4FF; --red:#CF1322; --redbg:#FFF1F0; --green:#166534; --greenbg:#F6FFED; }
  * { box-sizing:border-box; }
  body { font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
    color:var(--ink); background:var(--canvas); margin:0; font-size:14px; line-height:1.5; }
  .shell { max-width:760px; margin:0 auto; padding:36px 24px 64px; }
  .brand { display:flex; align-items:center; gap:9px; border-bottom:1px solid var(--line);
    padding-bottom:14px; margin-bottom:24px; }
  .brand .name { font-weight:600; font-size:16px; }
  .brand .name sup { font-size:8px; font-weight:500; vertical-align:super; }
  h1 { font-size:20px; font-weight:600; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 20px; }
  .card { background:var(--card); border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,0.06);
    padding:18px 20px; margin:16px 0; }
  label { display:block; font-weight:500; margin:0 0 6px; }
  input[type=text], input[type=password], textarea { width:100%; font:inherit; color:var(--ink);
    border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:#fff; }
  input:focus, textarea:focus { outline:none; border-color:var(--blue);
    box-shadow:0 0 0 3px rgba(1,92,230,0.12); }
  textarea { resize:vertical; min-height:44px; }
  .field { margin-bottom:14px; }
  button { font:inherit; font-weight:500; color:#fff; background:var(--blue); border:none;
    border-radius:6px; padding:9px 16px; cursor:pointer; }
  button:hover { background:var(--blue2); }
  a { color:var(--blue); text-decoration:none; }
  .pill { display:inline-block; background:var(--tint); color:var(--blue); font-size:12px;
    font-weight:500; border-radius:6px; padding:3px 9px; }
  .muted { color:var(--muted); }
  .hint { font-size:12px; color:var(--muted); margin-top:8px; }
  .alert { border-radius:6px; padding:9px 12px; font-size:13px; margin:12px 0; }
  .alert.err { background:var(--redbg); color:var(--red); border:1px solid #FFCCC7; }
  .alert.ok  { background:var(--greenbg); color:var(--green); border:1px solid #B7EB8F; }
  .q { padding:14px 0; border-bottom:1px solid var(--line2); }
  .q:last-of-type { border-bottom:none; }
  .q .num { color:var(--muted); font-weight:500; }
  .bar { display:flex; align-items:center; justify-content:space-between; gap:12px;
    margin-top:18px; }
</style>"""

HEADER = (
    f'<div class="brand">{LOGOMARK}<span class="name">SecurityPal<sup>&trade;</sup>'
    "</span></div>"
)


def page(title: str, inner: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title>{STYLE}</head><body><div class=\"shell\">"
        f"{HEADER}{inner}</div></body></html>"
    )


def login_page(error: str = "", expired: bool = False) -> str:
    banner = ""
    if error:
        banner = f'<div class="alert err">{html.escape(error)}</div>'
    elif expired:
        banner = '<div class="alert err">Your session expired. Please log in again.</div>'
    inner = f"""
      <h1>Vendor Security Portal</h1>
      <p class="sub">Sign in to complete your assigned security questionnaire.</p>
      <div class="card" style="max-width:420px">
        {banner}
        <form method="post" action="/login">
          <div class="field">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" autocomplete="username">
          </div>
          <div class="field">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" autocomplete="current-password">
          </div>
          <button type="submit">Log in</button>
          <p class="hint">For security, the password rotates every hour. Request the
          current password from the support desk by email.</p>
        </form>
      </div>
    """
    return page("Sign in — SecurityPal Vendor Portal", inner)


def questionnaire_page(answers: dict, error: str = "") -> str:
    banner = f'<div class="alert err">{html.escape(error)}</div>' if error else ""
    rows = []
    for i, q in enumerate(QUESTIONS, start=1):
        qid = q["id"]
        val = html.escape(answers.get(qid, ""))
        rows.append(
            f'<div class="q"><label for="answer_{qid}">'
            f'<span class="num">{i}.</span> {html.escape(q["question"])}</label>'
            f'<textarea id="answer_{qid}" name="answer_{qid}" rows="2" '
            f'placeholder="Type your answer">{val}</textarea></div>'
        )
    inner = f"""
      <h1>Security questionnaire</h1>
      <p class="sub">Answer every question, then submit.
        <span class="pill">{len(QUESTIONS)} questions</span></p>
      {banner}
      <form method="post" action="/questionnaire">
        <div class="card">
          {''.join(rows)}
        </div>
        <div class="bar">
          <span class="muted">All questions are required.</span>
          <button type="submit">Submit questionnaire</button>
        </div>
      </form>
    """
    return page("Security questionnaire — SecurityPal Vendor Portal", inner)


def confirmation_page(answered: int) -> str:
    inner = f"""
      <h1>Questionnaire submitted</h1>
      <p class="sub">Thank you — your responses have been recorded.</p>
      <div class="card" style="max-width:460px">
        <div class="alert ok">Submitted successfully — {answered} of {len(QUESTIONS)}
        questions answered.</div>
        <p class="muted">You may close this window.</p>
      </div>
    """
    return page("Submitted — SecurityPal Vendor Portal", inner)


# --------------------------------------------------------------------------- #
# Routes                                                                      #
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def home(expired: int = 0):
    return login_page(expired=bool(expired))


@app.post("/login")
def login(username: str = Form(""), password: str = Form("")):
    if username.strip() != PORTAL_USERNAME or password.strip().upper() != current_password():
        return HTMLResponse(
            login_page(error="Incorrect username or password."), status_code=401
        )
    sid = secrets.token_hex(16)
    SESSIONS[sid] = {"hour": _hour(), "answers": {}, "submitted": False}
    resp = RedirectResponse(url="/questionnaire", status_code=303)
    resp.set_cookie("sp_session", sid, httponly=True, samesite="lax")
    return resp


@app.get("/questionnaire", response_class=HTMLResponse)
def show_questionnaire(request: Request):
    _, s = _get_session(request)
    if s is None:
        return RedirectResponse(url="/?expired=1", status_code=303)
    if s["submitted"]:
        return HTMLResponse(confirmation_page(len(s["answers"])))
    return HTMLResponse(questionnaire_page(s["answers"]))


@app.post("/questionnaire", response_class=HTMLResponse)
async def submit_questionnaire(request: Request):
    _, s = _get_session(request)
    if s is None:
        return RedirectResponse(url="/?expired=1", status_code=303)

    form = await request.form()
    answers = {}
    for qid in QUESTION_IDS:
        val = (form.get(f"answer_{qid}") or "").strip()
        if val:
            answers[qid] = val
    s["answers"] = answers

    missing = [qid for qid in QUESTION_IDS if qid not in answers]
    if missing:
        return HTMLResponse(
            questionnaire_page(
                answers,
                error=f"{len(missing)} question(s) still need an answer before you can submit.",
            ),
            status_code=400,
        )

    s["submitted"] = True
    return HTMLResponse(confirmation_page(len(answers)))
