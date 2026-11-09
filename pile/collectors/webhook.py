"""Anything can push items in: POST /ingest on `pile serve`, with your local token.

Use it from Tasker or MacroDroid on Android, an ntfy subscription, a cron job, a CI step:

    curl -X POST http://127.0.0.1:8765/ingest -H "Authorization: Bearer $(pile token)" \\
      -H "Content-Type: application/json" \\
      -d '{"app": "Deploys", "title": "prod is green", "body": "v1.4.2", "url": "https://ci.example.com/42"}'

Fields: app (required), title or body (one required), sender, body, url, id (dedupe key), at (unix seconds).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time

NAME = "webhook"
PLATFORMS = ("any",)
NEEDS = "`pile serve` running; send with your token (`pile token`)"
ORDER = 60


def check(**_):
    return True, "POST /ingest while `pile serve` runs (token: `pile token`)"


def token(store) -> str:
    t = store.get_meta("webhook.token")
    if not t:
        t = "pile_" + secrets.token_urlsafe(24)
        store.set_meta("webhook.token", t)
    return t


def authorized(store, header: str) -> bool:
    given = (header or "").replace("Bearer ", "", 1).strip()
    return bool(given) and secrets.compare_digest(given, token(store))


def check_url(url: str) -> str:
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if url and (not scheme.replace("-", "").replace("+", "").replace(".", "").isalnum()
                or scheme in ("javascript", "data", "file", "vbscript", "bundle", "shell")):
        raise ValueError("url must be a web or app link")
    return url


def ingest(store, p: dict) -> dict:
    if not isinstance(p, dict):
        raise ValueError("expected a JSON object")
    if p.get("origin"):
        return forwarded(store, p)
    app = str(p.get("app") or "").strip()
    title, body = str(p.get("title") or "").strip(), str(p.get("body") or "").strip()
    if not app or not (title or body):
        raise ValueError("need app and a title or body")
    sender = str(p.get("sender") or title or app)[:200]
    at = float(p.get("at") or time.time())
    ext = str(p.get("id") or hashlib.sha1(("%s|%s|%s|%s" % (app, title, body, at)).encode()).hexdigest())
    url = check_url(str(p.get("url") or ""))
    item_id = store.upsert({
        "source": str(p.get("source") or NAME)[:40], "ext_id": ext[:200], "app": "hook:" + app.lower()[:80],
        "app_name": app[:80], "thread": "hook:%s|%s" % (app.lower(), sender), "sender": sender,
        "title": title[:500] if sender != title else "", "body": (body or title)[:2000], "at": at, "url": url[:2000]})
    return {"id": item_id, "muted": item_id is None}


ORIGIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")


def forwarded(store, p: dict) -> dict:
    """An item another pile collected (`pile forward`): kept as it was there, its source marked "<origin>:<source>"."""
    origin = str(p.get("origin") or "").strip().lower()
    if not ORIGIN.match(origin):
        raise ValueError("origin: lowercase letters, digits and dashes")
    src, ext, app = (str(p.get(k) or "").strip() for k in ("source", "id", "app"))
    if not (src and ext and app):
        raise ValueError("a forwarded item needs source, id and app")
    try:
        url = check_url(str(p.get("url") or ""))
    except ValueError:
        url = ""  # a link only the sending machine can open (bundle:...): the item is still worth having
    label = p.get("label") if p.get("label") in ("needs-you", "fyi", "noise") else None
    item_id = store.upsert({
        "source": ("%s:%s" % (origin, src))[:60], "ext_id": ext[:200], "app": app[:200],
        "app_name": str(p.get("app_name") or app)[:80], "thread": str(p.get("thread") or "")[:500] or "%s|%s" % (app, p.get("sender") or ext),
        "sender": str(p.get("sender") or "")[:200], "title": str(p.get("title") or "")[:500],
        "body": str(p.get("body") or "")[:2000], "at": float(p.get("at") or time.time()), "url": url[:2000],
        "label": label, "label_by": "forward" if label else None})
    return {"id": item_id, "muted": item_id is None}
