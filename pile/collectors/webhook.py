"""Anything can push items in: POST /ingest on `pile serve`, with your local token.

Use it from Tasker or MacroDroid on Android, an ntfy subscription, a cron job, a CI step:

    curl -X POST http://127.0.0.1:8765/ingest -H "Authorization: Bearer $(pile token)" \\
      -H "Content-Type: application/json" \\
      -d '{"app": "Deploys", "title": "prod is green", "body": "v1.4.2", "url": "https://ci.example.com/42"}'

Fields: app (required), title or body (one required), sender, body, url, id (dedupe key), at (unix seconds),
actions: up to 4 row buttons, [{label, url, method?, headers?, headersFromFile?, body?, confirm?}]. Pile's server makes
the request (so a secret in headersFromFile, a file under ~/.config, never reaches the page), only to a loopback URL,
and marks the item done when it answers 2xx.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit

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


LOOPBACK = ("127.0.0.1", "localhost", "::1")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
HEADER = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def loopback(url: str) -> bool:
    try:
        u = urlsplit(url)
        return u.scheme == "http" and u.hostname in LOOPBACK and not u.username and not u.password
    except ValueError:
        return False


def secret_file(path: str) -> str:
    """headersFromFile may only name a file under ~/.config, so an item can't make pile send any file it can read."""
    full = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(os.path.expanduser("~/.config"))
    if not full.startswith(root + os.sep):
        raise ValueError("headersFromFile: a file under ~/.config")
    return path


def check_actions(acts) -> list:
    if acts in (None, []):
        return []
    if not isinstance(acts, list) or len(acts) > 4:
        raise ValueError("actions: a list of at most 4")
    out = []
    for i, a in enumerate(acts):
        if not isinstance(a, dict):
            raise ValueError("actions[%d]: an object" % i)
        label, url = str(a.get("label") or "").strip(), str(a.get("url") or "").strip()
        method = str(a.get("method") or "POST").upper()
        if not label or len(label) > 40:
            raise ValueError("actions[%d].label: 1 to 40 characters" % i)
        if not loopback(url) or len(url) > 2000:
            raise ValueError("actions[%d].url: http://127.0.0.1 or localhost only" % i)
        if method not in METHODS:
            raise ValueError("actions[%d].method: one of %s" % (i, ", ".join(METHODS)))
        b = {"label": label, "url": url, "method": method}
        for k in ("headers", "headersFromFile"):
            h = a.get(k) or {}
            if not isinstance(h, dict) or not all(HEADER.match(str(n)) and isinstance(v, str) for n, v in h.items()):
                raise ValueError("actions[%d].%s: {name: string}" % (i, k))
            if h:
                b[k] = {n: (secret_file(v) if k == "headersFromFile" else v) for n, v in h.items()}
        if a.get("body") is not None:
            if not isinstance(a["body"], (dict, list, str)) or len(json.dumps(a["body"])) > 8192:
                raise ValueError("actions[%d].body: JSON or a string, at most 8 KB" % i)
            b["body"] = a["body"]
        if a.get("confirm"):
            b["confirm"] = str(a["confirm"])[:200]
        out.append(b)
    return out


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
    buttons = check_actions(p.get("actions"))
    item_id = store.upsert({"buttons": buttons,
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
