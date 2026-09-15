"""Playwright-based browser automation for the vendor portal (login, read, submit)."""
import argparse
import json
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from pipeline.portal.parser import detect_state, parse_questions

log = logging.getLogger(__name__)


class PortalBrowser:
    """Sync Playwright session against the vendor portal."""

    def __init__(self, settings, out_dir: Path | None = None):
        self.settings = settings
        self.out_dir = Path(out_dir) if out_dir else None
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=settings.PORTAL_HEADLESS,
            slow_mo=settings.PORTAL_SLOW_MO_MS,
        )
        self._context = None
        self._page = None
        self.new_session()

    def new_session(self):
        """Fresh cookie jar. Call this after expiry before logging in again."""
        if self._context:
            self._context.close()
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def login(self, url: str, user: str, pw: str) -> str:
        """ok if we land on /questionnaire, else bad_credentials."""
        self._page.goto(url)
        self._page.fill("#username", user)
        self._page.fill("#password", pw)
        self._page.click("button[type=submit]")
        self._page.wait_for_load_state()
        if self._page.url.rstrip("/").endswith("/questionnaire"):
            return "ok"
        return "bad_credentials"

    def state(self) -> str:
        return detect_state(self._page.url, self._page.content())

    def questions(self):
        return parse_questions(self._page.content())

    def submit(self, answers: dict[str, str]) -> str:
        """Fill answer_<id> textareas, submit, return detect_state. Screenshot on success."""
        for qid, text in answers.items():
            self._page.fill(f'textarea[name="answer_{qid}"]', text)
        self._page.click("button[type=submit]")
        self._page.wait_for_load_state()
        html = self._page.content()
        state = detect_state(self._page.url, html)
        if state == "submitted" and self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(self.out_dir / "submitted.png"))
        return state

    def close(self):
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None


## Everything under this is for testing the functionality of extracting qns + manually added delay + asnwering the questions and saving a snapshot of submission.

if __name__ == "__main__":
    import time

    from pipeline.config.config import load_settings
    from pipeline.config.logging_setup import setup_logging

    p = argparse.ArgumentParser()
    p.add_argument("--password", required=True)
    p.add_argument("--url")
    p.add_argument("--delay", type=float, default=5, help="seconds closed between read and submit")
    p.add_argument("--slow", type=int, default=250, help="playwright slow_mo ms so you can watch")
    a = p.parse_args()
    setup_logging()
    s = load_settings()
    s.PORTAL_HEADLESS = False
    s.PORTAL_SLOW_MO_MS = a.slow
    url = a.url or s.PORTAL_BASE_URL
    out = s.OUTPUT_DIR / "portal"
    out.mkdir(parents=True, exist_ok=True)

    b = PortalBrowser(s, out_dir=out)
    try:
        status = b.login(url, s.PORTAL_USERNAME, a.password)
        print("login", status, "state", b.state())
        if status != "ok":
            raise SystemExit(1)
        qs = b.questions()
        (out / "questions.json").write_text(
            json.dumps([q.__dict__ for q in qs], indent=2), encoding="utf-8"
        )
        print("n", len(qs))
        for q in qs:
            print(f"{q.id}\t{q.text}")
        print("wrote", out / "questions.json")
    finally:
        b.close()

    print("browser closed, waiting", a.delay, "s")
    time.sleep(a.delay)

    b = PortalBrowser(s, out_dir=out)
    try:
        status = b.login(url, s.PORTAL_USERNAME, a.password)
        st = b.state()
        print("login2", status, "state", st)
        if status != "ok":
            raise SystemExit(1)
        if st == "questionnaire":
            dummy = {q.id: f"dummy {q.id}" for q in qs}
            st = b.submit(dummy)
        print("submit", st)
    finally:
        b.close()
