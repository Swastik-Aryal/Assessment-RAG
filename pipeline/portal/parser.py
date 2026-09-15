"""Parse portal HTML: extract questions from the questionnaire and detect page state."""
import argparse
import re

from bs4 import BeautifulSoup

from pipeline.models import Question

_NUM = re.compile(r"^\d+\.\s*")


def parse_questions(html: str) -> list[Question]:
    """Id from textarea name (answer_Q1 -> Q1); label text minus the leading n."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for block in soup.select("div.q"):
        ta = block.find("textarea")
        lab = block.find("label")
        if ta is None or lab is None:
            continue
        name = ta.get("name") or ""
        qid = name.removeprefix("answer_")
        text = _NUM.sub("", lab.get_text(" ", strip=True))
        if qid and text:
            out.append(Question(id=qid, text=text))
    return out


def detect_state(url: str, html: str) -> str:
    """expired | submitted | incomplete | questionnaire | login."""
    if "expired=1" in url:
        return "expired"
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if title == "Questionnaire submitted":
        return "submitted"
    if title == "Security questionnaire":
        if soup.select_one(".alert.err"):
            return "incomplete"
        return "questionnaire"
    return "login"


if __name__ == "__main__":
    import json

    from pipeline.config.config import load_settings
    from pipeline.config.logging_setup import setup_logging
    from pipeline.portal.browser import PortalBrowser

    p = argparse.ArgumentParser()
    p.add_argument("--password", required=True)
    p.add_argument("--url")
    a = p.parse_args()
    setup_logging()
    s = load_settings()
    out = s.OUTPUT_DIR / "portal"
    out.mkdir(parents=True, exist_ok=True)
    b = PortalBrowser(s)
    try:
        status = b.login(a.url or s.PORTAL_BASE_URL, s.PORTAL_USERNAME, a.password)
        print("login", status)
        if status != "ok":
            raise SystemExit(1)
        print("state", b.state())
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
