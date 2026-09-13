"""Shared config + helpers used by both mock services (mail + portal).

Nothing here is secret in a real sense — it exists so the mock mail server and
the mock portal agree on the hourly password without a network round-trip
between themselves. The *candidate's* pipeline is NOT allowed to import this
module: it must obtain the current portal password over email, exactly as it
would against a real vendor portal.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

# --- Network layout (override via env if a port is already taken) ------------
MAIL_HOST = os.environ.get("MOCK_MAIL_HOST", "127.0.0.1")
MAIL_PORT = int(os.environ.get("MOCK_MAIL_PORT", "8025"))
MAIL_BASE_URL = f"http://{MAIL_HOST}:{MAIL_PORT}"

PORTAL_HOST = os.environ.get("MOCK_PORTAL_HOST", "127.0.0.1")
PORTAL_PORT = int(os.environ.get("MOCK_PORTAL_PORT", "8080"))
PORTAL_BASE_URL = f"http://{PORTAL_HOST}:{PORTAL_PORT}"

# --- Fixed mailbox addresses used by the scenario ----------------------------
# The address the candidate's pipeline reads from / sends as:
PIPELINE_ADDRESS = "pipeline@securitypal.test"
# The (simulated) client who sends the questionnaire and expects it back:
CLIENT_ADDRESS = "vendor-assessments@acme-client.test"
# The portal support desk that hands out the current hourly password on request:
PORTAL_ACCESS_ADDRESS = "access@portal.acme-client.test"

# --- Portal login -------------------------------------------------------------
PORTAL_USERNAME = "securitypal"
# Rotates every hour. Both mock services derive it from the same secret + hour
# bucket, so the portal and the "support desk" bot always agree on the value
# that is valid *right now*.
_PORTAL_SECRET = os.environ.get("MOCK_PORTAL_SECRET", "assessment-portal-secret-v1")


def current_password(ts: float | None = None) -> str:
    """Deterministic password for the hour containing `ts` (default: now)."""
    ts = time.time() if ts is None else ts
    hour_bucket = int(ts // 3600)
    digest = hmac.new(
        _PORTAL_SECRET.encode(), str(hour_bucket).encode(), hashlib.sha256
    ).hexdigest()
    return digest[:8].upper()


def seconds_until_rotation(ts: float | None = None) -> int:
    ts = time.time() if ts is None else ts
    return int(3600 - (ts % 3600))
