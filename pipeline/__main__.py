"""Watch loop entry point: poll inbox, classify, ack, and dispatch excel/portal flows.

Usage: python -m pipeline

Logging:
    outputs/<session>/                  created at startup (<session> = UTC timestamp)
        runs.log                        session-level log (INFO by default, set LOG_LEVEL in .env)
        <run_id>/                       one folder per questionnaire processed
            run.log                     per-run log (DEBUG, captures every question + LLM call)
            request.json, answers.json, retrieval.json, prompts.json, schema.json, ...

    OUTPUT_DIR in .env controls the root (default: outputs/).
    Console mirrors the session log at the same level.

IMPORTANT: 

- MAIL SERVER NEEDS TO BE UP FOR THIS TO WORK!

DESIGN DECISION:
- If on EXCEL mode and the question processing begins, 
    THEN switching to PORTAL MODE will not stop the current process BUT NO Confirmation email will be sent. (AND VICE VERSA)
    This is to ensure we never send blind emails that were never acknowledged.

"""
import logging
import time
from datetime import datetime, timezone

import requests

from pipeline.config.config import load_settings
from pipeline.config.logging_setup import setup_logging

log = logging.getLogger(__name__)


def main():
    settings = load_settings()
    session_dir = settings.OUTPUT_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    setup_logging(settings.LOG_LEVEL, session_dir / "runs.log")
    try:
        requests.get(settings.MAIL_BASE_URL.rstrip("/") + "/inbox", timeout=3).raise_for_status()
    except requests.RequestException:
        log.error("mail server down")
        raise SystemExit("mail server down")
    log.info("mail server up")

    from pipeline.flows import record_failure, run_excel, run_portal
    from pipeline.llm.gemini import GeminiClient
    from pipeline.llm.ollama import OllamaClient
    from pipeline.mail.client import MailClient
    from pipeline.mail.watcher import Watcher, classify
    from pipeline.models import Scenario
    from pipeline.rag.generator import Generator
    from pipeline.rag.index import build_retriever

    gemini = GeminiClient(settings)
    log.info("checking gemini")
    gemini.generate("Reply with the single word ok.", "ok")
    log.info("checking ollama")
    ollama = OllamaClient(settings)
    ollama.generate("Reply with the single word ok.", "ok")
    log.info("llm smoke ok")
    log.info("loading retriever")
    retriever = build_retriever(settings)
    try:
        gen = Generator(ollama, retriever)
        mail = MailClient(settings)
        watcher = Watcher(settings, mail)
        log.info("watching inbox")
        while True:
            pending = watcher.pending()
            if pending:
                log.info("checking mailbox: %s pending", len(pending))
            else:
                log.debug("checking mailbox: empty")
            for msg in pending:
                kind = classify(msg, settings)
                log.info("found %s %s %r", msg.id, kind, msg.subject)
                if watcher.sent_ack(msg.id):
                    log.info("already acknowledged %s, resuming", msg.id)
                watcher.ack(msg)
                try:
                    if kind == Scenario.EXCEL:
                        log.info("starting excel %s", msg.id)
                        run_excel(
                            msg, settings, watcher, gemini, gen, session_dir,
                            on_progress=watcher.ack_pending,
                        )
                    elif kind == Scenario.PORTAL:
                        log.info("starting portal %s", msg.id)
                        run_portal(
                            msg, settings, watcher, gen, session_dir,
                            on_progress=watcher.ack_pending,
                        )
                    else:
                        log.warning("skip unknown %s", msg.id)
                except Exception as exc:
                    log.exception("failed %s", msg.id)
                    watcher.failed.add((msg.id, msg.received_at))
                    record_failure(session_dir, msg, kind, exc)
                st = mail.status()
                log.info(
                    "mailbox ack=%s delivered=%s",
                    st.get("acknowledged"),
                    st.get("delivered_back"),
                )
            time.sleep(settings.POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log.info("stop")
    finally:
        retriever.close()


if __name__ == "__main__":
    main()
