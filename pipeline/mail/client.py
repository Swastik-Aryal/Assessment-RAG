"""HTTP client for the mock mailbox API (inbox, send, sent, status, reset)."""
import argparse
import base64
import json
import time
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pipeline.config.config import load_settings


@dataclass
class Attachment:
    """Filename plus raw bytes (decoded from the mail API's content_b64)."""
    filename: str
    content: bytes = b""


@dataclass
class Message:
    """Full inbox message. `id` is msg-NNNN, shared with sent mail."""
    id: str
    sender: str
    subject: str
    body: str
    received_at: str
    attachments: list[Attachment] = field(default_factory=list)


class MailClient:
    """HTTP client for the mock mailbox."""

    def __init__(self, settings):
        self.settings = settings
        self.base = settings.MAIL_BASE_URL.rstrip("/")
        self.http = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.http.mount("http://", HTTPAdapter(max_retries=retry))

    def _req(self, method, path, **kw):
        """One request against MAIL_BASE_URL. Raises on non-2xx."""
        r = self.http.request(method, self.base + path, timeout=10, **kw)
        r.raise_for_status()
        return r

    def list_inbox(self) -> list[dict]:
        """Inbox summaries: id, from, to, subject, snippet, has_attachments, received_at."""
        return self._req("GET", "/inbox").json()

    def get_message(self, msg_id) -> Message:
        """Full message including body and decoded attachments."""
        d = self._req("GET", f"/inbox/{msg_id}").json()
        atts = [Attachment(a["filename"], base64.b64decode(a["content_b64"])) for a in d["attachments"]]
        return Message(d["id"], d["from"], d["subject"], d["body"] or "", d["received_at"], atts)

    def send(self, to, subject, body, attachments=()) -> str:
        """Send mail as the pipeline. Returns the new message id."""
        payload = {
            "to": to,
            "subject": subject,
            "body": body,
            "attachments": [
                {"filename": a.filename, "content_b64": base64.b64encode(a.content).decode()}
                for a in attachments
            ],
        }
        return self._req("POST", "/send", json=payload).json()["id"]

    def list_sent(self) -> list[dict]:
        """Everything this pipeline address has sent (used to derive ack/completed)."""
        return self._req("GET", "/sent").json()

    def status(self) -> dict:
        """Grader snapshot: scenario, acknowledged, delivered_back, counts."""
        return self._req("GET", "/admin/status").json()

    def reset(self, scenario) -> dict:
        """Wipe the mailbox and seed excel or portal."""
        return self._req("POST", "/admin/reset", params={"scenario": scenario}).json()

    def wait_for(self, sender, exclude_ids, timeout=60) -> Message:
        """Poll inbox until a new mail from `sender` appears, else TimeoutError."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for s in self.list_inbox():
                if s["id"] not in exclude_ids and s["from"] == sender:
                    return self.get_message(s["id"])
            time.sleep(2)
        raise TimeoutError(f"no reply from {sender} within {timeout}s")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--reset", choices=["excel", "portal"])
    p.add_argument("--inbox", action="store_true")
    p.add_argument("--sent", action="store_true")
    p.add_argument("--status", action="store_true")
    a = p.parse_args()
    mail = MailClient(load_settings())
    if a.reset:
        print(mail.reset(a.reset))
    if a.inbox:
        print(json.dumps(mail.list_inbox(), indent=1))
    if a.sent:
        print(json.dumps(mail.list_sent(), indent=1))
    if a.status:
        print(mail.status())
