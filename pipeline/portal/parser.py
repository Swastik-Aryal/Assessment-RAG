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


