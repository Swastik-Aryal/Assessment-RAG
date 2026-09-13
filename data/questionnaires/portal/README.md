# Type B — Portal questionnaire

There is no file to hand out for the portal scenario. The questions are served
by the **mock portal** (`mocks/portal_server.py`) behind a login whose password
rotates hourly.

Reset the mailbox to the portal scenario, then follow the flow in
[`../../../mocks/README.md`](../../../mocks/README.md):

```bash
curl -X POST "http://127.0.0.1:8025/admin/reset?scenario=portal"
```

The seeded client email tells your pipeline where the portal is and how to
request the current password.
