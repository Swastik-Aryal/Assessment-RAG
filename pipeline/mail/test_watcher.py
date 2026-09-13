from pipeline.config import Settings
from pipeline.mail.client import Attachment, Message
from pipeline.mail.watcher import classify, parse_portal_request
from pipeline.models import Scenario

S = Settings.model_construct(
    CLIENT_ADDRESS="vendor-assessments@acme-client.test",
    PORTAL_BASE_URL="http://127.0.0.1:8080",
    PORTAL_USERNAME="securitypal",
    PORTAL_ACCESS_ADDRESS="access@portal.acme-client.test",
)


def msg(sender=S.CLIENT_ADDRESS, subject="Please complete", body="hello", attachments=()):
    return Message("msg-0001", sender, subject, body, "2026-01-01T00:00:00", list(attachments))


def test_classify():
    assert classify(msg(attachments=[Attachment("Q.XLSX")]), S) == Scenario.EXCEL
    assert classify(msg(attachments=[Attachment("q.xlsm")]), S) == Scenario.EXCEL
    assert classify(msg(body="see http://127.0.0.1:8080"), S) == Scenario.PORTAL
    assert classify(msg(body="use the Portal"), S) == Scenario.PORTAL
    assert classify(msg(subject="Re: anything", body="portal"), S) == Scenario.UNKNOWN
    assert classify(msg(sender="access@portal.acme-client.test", body="portal http://x"), S) == Scenario.UNKNOWN
    assert classify(msg(body="no file and no url"), S) == Scenario.UNKNOWN


def test_parse_portal_request_with_fallbacks():
    p = parse_portal_request(msg(body="Go to http://example.test:9 and email access@portal.acme-client.test"), S)
    assert p.portal_url == "http://example.test:9"
    assert p.username == "securitypal"
    assert p.access_address == "access@portal.acme-client.test"
