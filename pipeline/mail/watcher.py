import logging
import re
from dataclasses import dataclass

from pipeline.config import load_settings
from pipeline.logging_setup import setup_logging
from pipeline.mail.client import MailClient, Message
from pipeline.models import Scenario

log = logging.getLogger(__name__)


@dataclass
class PortalRequest:
    """Parsed portal URL, username, and access-desk address from the client email."""
    portal_url: str
    username: str
    access_address: str


def classify(msg: Message, settings) -> str:
    """EXCEL / PORTAL / UNKNOWN. Ignores non-client senders and Re: confirmations."""
    if msg.sender != settings.CLIENT_ADDRESS or msg.subject.startswith("Re:"):
        return Scenario.UNKNOWN
    if any(a.filename.lower().endswith((".xlsx", ".xlsm")) for a in msg.attachments):
        return Scenario.EXCEL
    if re.search(r"https?://", msg.body) or "portal" in msg.body.lower():
        return Scenario.PORTAL
    return Scenario.UNKNOWN


def parse_portal_request(msg: Message, settings) -> PortalRequest:
    """Pull portal URL, username, and access@ address from the body; fall back to settings."""
    url = re.search(r"https?://\S+", msg.body)
    user = re.search(r"username:\s*(\S+)", msg.body, re.I)
    access = re.search(r"[\w.+-]+@portal[\w.-]*", msg.body)
    return PortalRequest(
        url.group(0).rstrip(".,;") if url else settings.PORTAL_BASE_URL,
        user.group(1) if user else settings.PORTAL_USERNAME,
        access.group(0) if access else settings.PORTAL_ACCESS_ADDRESS,
    )


def ref(msg_id: str) -> str:
    """Marker we put in sent bodies so ack/completed can be derived from /sent."""
    return f"[ref:{msg_id}]"


class Watcher:
    """Derives request state from the mailbox itself: a request is acknowledged if we sent
    a mail carrying its ref marker, completed if that mail's subject starts with 'Completed:'."""

    def __init__(self, settings, mail: MailClient):
        self.settings = settings
        self.mail = mail
        self.failed: set[str] = set()
        self._cache: dict[str, Message] = {}

    def _sent_refs(self) -> tuple[set[str], set[str]]:
        """(acked ids, completed ids) from [ref:msg-N] markers in sent mail."""
        acked, completed = set(), set()
        for s in self.mail.list_sent():
            m = re.search(r"\[ref:(msg-\d+)\]", s.get("body") or "")
            if not m:
                continue
            acked.add(m.group(1))
            if s["subject"].startswith("Completed:"):
                completed.add(m.group(1))
        return acked, completed

    def _requests(self) -> list[Message]:
        """Client questionnaire mails, oldest first. Password replies and Re: are skipped."""
        out = []
        for s in self.mail.list_inbox():
            if s["from"] != self.settings.CLIENT_ADDRESS or s["subject"].startswith("Re:"):
                continue
            msg = self._cache.get(s["id"]) or self._cache.setdefault(s["id"], self.mail.get_message(s["id"]))
            if classify(msg, self.settings) != Scenario.UNKNOWN:
                out.append(msg)
        return sorted(out, key=lambda m: (m.received_at, m.id))

    def ack_pending(self) -> int:
        """Send Re: <subject> with [ref:] for every unacknowledged request. Returns how many."""
        acked, _ = self._sent_refs()
        n = 0
        for msg in self._requests():
            if msg.id in acked:
                continue
            self.mail.send(
                self.settings.CLIENT_ADDRESS,
                "Re: " + msg.subject,
                f"We have received your questionnaire request and will complete it shortly. {ref(msg.id)}",
            )
            log.info("acknowledged %s (%s)", msg.id, msg.subject)
            n += 1
        return n

    def pending(self) -> list[Message]:
        """Requests not yet Completed: and not in the in-memory failed set."""
        _, completed = self._sent_refs()
        return [m for m in self._requests() if m.id not in completed and m.id not in self.failed]


if __name__ == "__main__":
    setup_logging()
    settings = load_settings()
    w = Watcher(settings, MailClient(settings))
    acked, completed = w._sent_refs()
    for m in w._requests():
        kind = classify(m, settings)
        state = "completed" if m.id in completed else "acknowledged" if m.id in acked else "new"
        print(f"{m.id} {kind} {state} {m.subject!r}")
        if kind == Scenario.PORTAL:
            print("  ", parse_portal_request(m, settings))
