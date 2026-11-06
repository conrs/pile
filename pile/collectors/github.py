"""GitHub notifications. Token from GITHUB_TOKEN, else `gh auth token`."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

NAME = "github"
PLATFORMS = ("any",)
NEEDS = "a GitHub token (GITHUB_TOKEN, or the gh CLI logged in)"
ORDER = 40
API = "https://api.github.com"


def token() -> Optional[str]:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            return out.stdout.strip() or None
        except Exception:
            return None
    return None


def check(**_):
    if token():
        return True, "token found"
    return False, "no token: set GITHUB_TOKEN (notifications scope) or run `gh auth login`"


def html_url(n: dict) -> str:
    """API subject URL -> the page a human wants."""
    repo = (n.get("repository") or {}).get("html_url") or ""
    subject = n.get("subject") or {}
    url = subject.get("url") or ""
    m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/(\w+)/([^/]+)$", url)
    if not m:
        kind = subject.get("type")
        if kind == "CheckSuite":
            return repo + "/actions"
        if kind == "Discussion":
            return repo + "/discussions"
        return repo or "https://github.com/notifications"
    full, kind, ref = m.groups()
    base = "https://github.com/" + full
    if kind == "pulls":
        out = "%s/pull/%s" % (base, ref)
    elif kind == "issues":
        out = "%s/issues/%s" % (base, ref)
    elif kind == "commits":
        out = "%s/commit/%s" % (base, ref)
    elif kind == "releases":
        return base + "/releases"
    else:
        return base
    comment = re.search(r"/(?:issues|pulls)/comments/(\d+)$", subject.get("latest_comment_url") or "")
    return out + ("#issuecomment-" + comment.group(1) if comment else "")


def to_item(n: dict) -> dict:
    subject = n.get("subject") or {}
    repo = (n.get("repository") or {}).get("full_name") or ""
    at = datetime.strptime(n["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
    from calendar import timegm
    reason = (n.get("reason") or "").replace("_", " ")
    return {
        "source": NAME,
        "ext_id": str(n["id"]),
        "app": "github",
        "app_name": "GitHub",
        "thread": "github|" + str(n["id"]),
        "sender": repo,
        "title": subject.get("title") or "",
        "body": "%s · %s" % (subject.get("type") or "", reason) if reason else (subject.get("type") or ""),
        "at": float(timegm(at.timetuple())),
        "url": html_url(n),
    }


def fetch(tok: str) -> list:
    out, url = [], API + "/notifications?per_page=50"
    for _ in range(4):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok,
                                                   "Accept": "application/vnd.github+json",
                                                   "User-Agent": "pile"})
        with urllib.request.urlopen(req, timeout=20) as r:
            out += json.loads(r.read().decode())
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link") or "")
        if not nxt:
            break
        url = nxt.group(1)
    return out


def ingest(store, notifications: list) -> int:
    n = 0
    for note in notifications:
        if note.get("unread", True) and store.upsert(to_item(note)):
            n += 1
    store.close_missing(NAME, [str(x["id"]) for x in notifications if x.get("unread", True)])
    return n


def collect(store, notifications: Optional[list] = None, **_) -> int:
    if notifications is None:
        tok = token()
        if not tok:
            return 0
        notifications = fetch(tok)
    return ingest(store, notifications)


def _get(tok: str, url: str):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
                                               "User-Agent": "pile"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def search_item(it: dict, why: str) -> dict:
    from calendar import timegm
    repo = "/".join((it.get("repository_url") or "").split("/")[-2:])
    at = datetime.strptime(it["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
    return {"source": NAME, "ext_id": "search:%s" % it["id"], "app": "github", "app_name": "GitHub",
            "thread": "github|search:%s" % it["id"], "sender": repo, "title": it.get("title") or "",
            "body": why, "at": float(timegm(at.timetuple())), "url": it.get("html_url") or "", "tag": "unanswered"}


def backfill(store, days: float = 30, notifications=None, searches=None, **_) -> int:
    """Older notifications (read ones too), plus open review requests and assigned issues."""
    tok = None
    if notifications is None or searches is None:
        tok = token()
        if not tok:
            return 0
    import time as _time
    since = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - days * 86400))
    if notifications is None:
        notifications, url = [], API + "/notifications?all=true&per_page=50&since=" + since
        for page in range(1, 6):
            batch = _get(tok, url + "&page=%d" % page)
            notifications += batch
            if len(batch) < 50:
                break
    if searches is None:
        searches = []
        for q, why in (("is:open is:pr review-requested:@me", "review requested from you"),
                       ("is:open is:issue assignee:@me", "assigned to you")):
            res = _get(tok, API + "/search/issues?per_page=50&q=" + urllib.parse.quote(q))
            searches += [(it, why) for it in res.get("items", [])]
    n = 0
    for note in notifications:
        item = to_item(note)
        if not note.get("unread", True):
            item["tag"] = "earlier"
        if store.upsert(item):
            n += 1
    for it, why in searches:
        if store.upsert(search_item(it, why)):
            n += 1
    return n
