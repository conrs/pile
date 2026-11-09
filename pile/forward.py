"""`pile forward`: send what this pile collects to another pile (a hub) as it arrives.

Each item goes to the hub's POST /ingest with this machine's name (`--origin`) and the item's own source and id,
so the hub keeps it as `<origin>:<source>` and a resend lands on the same row: at least once, never twice.
What has gone is kept in a `forwarded` table (item id and the `at` that was sent), so an item a source
refreshes (a newer `at`) goes again and reopens on the hub. Unreachable hub: nothing is marked, and the next
round catches up, oldest first.

The token is never a flag: PILE_HUB_TOKEN, or a file (PILE_HUB_TOKEN_FILE, default ~/.config/pile/hub.token).
Behind Cloudflare Access with a service token: CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

SCHEMA = "CREATE TABLE IF NOT EXISTS forwarded (item_id INTEGER PRIMARY KEY, at REAL NOT NULL, sent REAL NOT NULL)"
BATCH = 200


class Unreachable(Exception):
    """The hub can't take anything right now (network, 401, 5xx): stop and try again later."""


def token_file(env=None) -> str:
    env = os.environ if env is None else env
    cfg = env.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return env.get("PILE_HUB_TOKEN_FILE") or os.path.join(cfg, "pile", "hub.token")


def token(env=None) -> str:
    env = os.environ if env is None else env
    t = (env.get("PILE_HUB_TOKEN") or "").strip()
    if t:
        return t
    try:
        with open(token_file(env), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def default_origin() -> str:
    name = socket.gethostname().split(".")[0].lower()
    return "".join(c if c.isalnum() else "-" for c in name).strip("-")[:24] or "remote"


def setup(store, since_s: float) -> None:
    """First run: forward what arrives from now on, plus the last `since_s` seconds; never the whole history."""
    store.db.execute(SCHEMA)
    if store.get_meta("forward.start_id") is None:
        top = store.db.execute("SELECT COALESCE(MAX(id), 0) FROM items").fetchone()[0]
        store.set_meta("forward.start_id", str(top))
        store.set_meta("forward.start_at", str(time.time() - since_s))


def pending(store, skip=(), limit=BATCH) -> list:
    """Items not yet sent, or refreshed since, oldest first. Done ones wait: if one reopens it goes then."""
    start_id = int(store.get_meta("forward.start_id") or 0)
    start_at = float(store.get_meta("forward.start_at") or 0)
    q = ("SELECT i.* FROM items i LEFT JOIN forwarded f ON f.item_id = i.id"
         " WHERE (f.item_id IS NULL OR i.at > f.at) AND i.state != 'done' AND (i.id > ? OR i.at >= ?)")
    args = [start_id, start_at]
    if skip:
        q += " AND i.source NOT IN (%s)" % ",".join("?" * len(skip))
        args += list(skip)
    q += " ORDER BY i.at, i.id LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in store.db.execute(q, args)]


def payload(store, item: dict, origin: str) -> dict:
    from . import rules
    link, _ = rules.resolve_link(store, dict(item))  # learned links travel; the hub can't learn the sender's
    return {"origin": origin, "source": item["source"], "id": item["ext_id"], "app": item["app"],
            "app_name": item["app_name"], "thread": item["thread"], "sender": item["sender"], "title": item["title"],
            "body": item["body"], "at": item["at"], "url": link or item["url"], "label": item["label"]}


def post(url: str, tok: str, body: dict, env=None, timeout=20) -> dict:
    """POST one item. Unreachable for anything worth retrying; ValueError for an item the hub refuses (4xx)."""
    env = os.environ if env is None else env
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + tok, "User-Agent": "pile-forward"}
    if env.get("CF_ACCESS_CLIENT_ID") and env.get("CF_ACCESS_CLIENT_SECRET"):
        headers["CF-Access-Client-Id"] = env["CF_ACCESS_CLIENT_ID"]
        headers["CF-Access-Client-Secret"] = env["CF_ACCESS_CLIENT_SECRET"]
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type") or ""
            text = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (400, 413, 422):
            raise ValueError("hub refused it (%d)" % e.code)
        raise Unreachable("hub answered %d" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise Unreachable(str(getattr(e, "reason", e)))
    # A login page (Cloudflare Access) answers 200 with HTML: that's not the hub.
    if not ctype.startswith("application/json"):
        raise Unreachable("not the hub (got %s; Cloudflare Access in the way?)" % (ctype.split(";")[0] or "no type"))
    try:
        return json.loads(text)
    except ValueError:
        raise Unreachable("not JSON from the hub")


def run_once(store, url, tok, origin, skip=(), send=post, log=None) -> dict:
    """Send everything pending. Stops at the first Unreachable, so nothing is marked that didn't arrive."""
    sent = refused = 0
    while True:
        batch = pending(store, skip)
        if not batch:
            return {"sent": sent, "refused": refused}
        for item in batch:
            try:
                send(url, tok, payload(store, item, origin))
                sent += 1
            except ValueError as e:  # this item will never go; don't let it block the rest
                refused += 1
                if log:
                    log("pile forward: item %d not taken: %s" % (item["id"], e))
            store.db.execute("INSERT INTO forwarded(item_id, at, sent) VALUES(?,?,?) ON CONFLICT(item_id)"
                             " DO UPDATE SET at=excluded.at, sent=excluded.sent", (item["id"], item["at"], time.time()))


def loop(store, url, tok, origin, skip=(), every=30, once=False, send=post, log=None) -> None:
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    said = None
    while True:
        try:
            r = run_once(store, url, tok, origin, skip, send, log)
            if r["sent"] or r["refused"]:
                log("%s sent %d%s" % (time.strftime("%H:%M:%S"), r["sent"], ", %d refused" % r["refused"] if r["refused"] else ""))
            if said:
                log("pile forward: hub reachable again")
            said = None
        except Unreachable as e:
            if str(e) != said:  # once per kind of failure, not every round
                log("pile forward: hub unreachable, will catch up: %s" % e)
            said = str(e)
            if once:
                raise
        if once:
            return
        time.sleep(every)
