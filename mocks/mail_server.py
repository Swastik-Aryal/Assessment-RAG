"""Mock mail server — a Gmail/Graph-style mailbox API for the assessment.

This stands in for a real email provider. Your pipeline talks to it exactly the
way it would talk to a hosted mailbox: read the inbox, download attachments,
and send replies. A built-in "counterparty" reacts to what you send — it plays
the role of the client who dispatched the questionnaire and of the portal
support desk that issues the hourly password.

Run:
    uvicorn mocks.mail_server:app --host 127.0.0.1 --port 8025

Everything is in-memory: restart (or POST /admin/reset) to start a run clean.

------------------------------------------------------------------------------
API (all JSON unless noted)
------------------------------------------------------------------------------
GET  /inbox                      -> [{id, from, to, subject, snippet, has_attachments, received_at}]
GET  /inbox/{id}                 -> full message incl. base64 attachments
GET  /inbox/{id}/attachments/{name} -> raw attachment bytes (binary)
POST /send                       -> body {from,to,subject,body,attachments:[{filename,content_b64}]}
GET  /sent                       -> messages your pipeline has sent (for your own debugging)
POST /admin/reset?scenario=excel|portal -> wipe + seed one scenario
GET  /admin/status               -> {scenario, delivered_back: bool, acknowledged: bool}
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mocks.shared import (
    CLIENT_ADDRESS,
    PIPELINE_ADDRESS,
    PORTAL_ACCESS_ADDRESS,
    PORTAL_BASE_URL,
    PORTAL_USERNAME,
    current_password,
    seconds_until_rotation,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPLEX_XLSX = (
    REPO_ROOT
    / "data"
    / "questionnaires"
    / "complex_excel"
    / "Industry Standard Questionnaires.xlsx"
)

app = FastAPI(title="Mock Mail Server", version="1.0")


# --- in-memory state ----------------------------------------------------------
class _State:
    def __init__(self) -> None:
        self.reset("excel")

    def reset(self, scenario: str) -> None:
        self.scenario = scenario
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self._next_id = 1
        self.acknowledged = False
        self.delivered_back = False
        if scenario == "excel":
            self._seed_excel()
        elif scenario == "portal":
            self._seed_portal()
        else:
            raise ValueError(f"unknown scenario: {scenario}")

    def _new_id(self) -> str:
        i = self._next_id
        self._next_id += 1
        return f"msg-{i:04d}"

    def deliver_to_inbox(
        self, sender: str, subject: str, body: str, attachments: Optional[list] = None
    ) -> dict:
        msg = {
            "id": self._new_id(),
            "from": sender,
            "to": PIPELINE_ADDRESS,
            "subject": subject,
            "body": body,
            "attachments": attachments or [],
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.inbox.append(msg)
        return msg

    def _seed_excel(self) -> None:
        attachments = []
        if COMPLEX_XLSX.exists():
            attachments.append(
                {
                    "filename": COMPLEX_XLSX.name,
                    "content_b64": base64.b64encode(COMPLEX_XLSX.read_bytes()).decode(),
                }
            )
        self.deliver_to_inbox(
            CLIENT_ADDRESS,
            "Security Questionnaire for Completion — ACME Client",
            (
                "Hello,\n\n"
                "Please find attached our vendor security questionnaire. Kindly "
                "confirm receipt by replying to this email, then return the "
                "completed workbook as an attachment when ready.\n\n"
                "Regards,\nACME Client Vendor Risk Team"
            ),
            attachments,
        )

    def _seed_portal(self) -> None:
        self.deliver_to_inbox(
            CLIENT_ADDRESS,
            "Security Questionnaire — Complete via Portal",
            (
                "Hello,\n\n"
                "Please complete our security questionnaire in our vendor portal:\n"
                f"    {PORTAL_BASE_URL}\n\n"
                f"    username: {PORTAL_USERNAME}\n\n"
                "For security, the portal password rotates every hour. Request the "
                f"current password by emailing {PORTAL_ACCESS_ADDRESS} with the "
                "subject line 'PORTAL ACCESS REQUEST'. Please confirm receipt of "
                "this email first.\n\n"
                "Regards,\nACME Client Vendor Risk Team"
            ),
        )

    # --- counterparty reactions to mail the pipeline sends ------------------
    def handle_outgoing(self, msg: dict) -> None:
        self.sent.append(msg)
        subject = (msg.get("subject") or "").lower()
        body = (msg.get("body") or "").lower()
        to = (msg.get("to") or "").lower()
        has_attach = bool(msg.get("attachments"))

        # 1) Password request -> support desk replies with the live password.
        if PORTAL_ACCESS_ADDRESS.lower() in to or "portal access" in subject:
            pw = current_password()
            self.deliver_to_inbox(
                PORTAL_ACCESS_ADDRESS,
                "Re: Portal Access Request — Temporary Password",
                (
                    f"Your temporary portal password is: {pw}\n"
                    f"It is valid for approximately {seconds_until_rotation() // 60} "
                    "more minutes, until the top of the next hour.\n\n"
                    f"Portal: {PORTAL_BASE_URL}  |  username: {PORTAL_USERNAME}"
                ),
            )
            return

        # 2) Completed questionnaire returned as an attachment (excel scenario).
        if has_attach and self.scenario == "excel":
            self.delivered_back = True
            self.deliver_to_inbox(
                CLIENT_ADDRESS,
                "Re: Security Questionnaire for Completion — Received",
                "Thank you — we have received your completed questionnaire.",
            )
            return

        # 3) Anything else addressed to the client counts as an acknowledgement.
        if CLIENT_ADDRESS.lower() in to:
            self.acknowledged = True


STATE = _State()


# --- request/response models --------------------------------------------------
class Attachment(BaseModel):
    filename: str
    content_b64: str


class OutgoingMail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(default=PIPELINE_ADDRESS, alias="from")
    to: str
    subject: str = ""
    body: str = ""
    attachments: list[Attachment] = []


# --- endpoints ----------------------------------------------------------------
def _summary(msg: dict) -> dict:
    return {
        "id": msg["id"],
        "from": msg["from"],
        "to": msg["to"],
        "subject": msg["subject"],
        "snippet": (msg["body"] or "")[:140],
        "has_attachments": bool(msg["attachments"]),
        "received_at": msg["received_at"],
    }


@app.get("/inbox")
def list_inbox():
    return [_summary(m) for m in STATE.inbox]


@app.get("/inbox/{msg_id}")
def get_message(msg_id: str):
    for m in STATE.inbox:
        if m["id"] == msg_id:
            return m
    raise HTTPException(404, f"no message {msg_id}")


@app.get("/inbox/{msg_id}/attachments/{filename}")
def get_attachment(msg_id: str, filename: str):
    for m in STATE.inbox:
        if m["id"] == msg_id:
            for a in m["attachments"]:
                if a["filename"] == filename:
                    return Response(
                        content=base64.b64decode(a["content_b64"]),
                        media_type="application/octet-stream",
                    )
    raise HTTPException(404, "attachment not found")


@app.post("/send")
def send_mail(mail: OutgoingMail):
    msg = {
        "id": STATE._new_id(),
        "from": mail.from_,
        "to": mail.to,
        "subject": mail.subject,
        "body": mail.body,
        "attachments": [a.model_dump() for a in mail.attachments],
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    STATE.handle_outgoing(msg)
    return {"status": "sent", "id": msg["id"]}


@app.get("/sent")
def list_sent():
    return [
        {k: v for k, v in m.items() if k != "attachments"}
        | {"attachment_names": [a["filename"] for a in m["attachments"]]}
        for m in STATE.sent
    ]


@app.post("/admin/reset")
def reset(scenario: str = "excel"):
    try:
        STATE.reset(scenario)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "reset", "scenario": scenario}


@app.get("/admin/status")
def status():
    return JSONResponse(
        {
            "scenario": STATE.scenario,
            "acknowledged": STATE.acknowledged,
            "delivered_back": STATE.delivered_back,
            "inbox_count": len(STATE.inbox),
            "sent_count": len(STATE.sent),
        }
    )
