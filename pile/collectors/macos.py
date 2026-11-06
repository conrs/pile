"""macOS Notification Center, read-only.

Reads the usernoted database. The terminal (or app) running pile needs Full Disk
Access: System Settings > Privacy & Security > Full Disk Access.
"""
from __future__ import annotations

import os
import plistlib
import sqlite3
import subprocess
from typing import Optional

from .. import deeplinks

NAME = "macos"
PLATFORMS = ("darwin",)
NEEDS = "Full Disk Access for your terminal"
ORDER = 10
PATH_OPT = "macos_db"
MAC_EPOCH = 978307200  # 2001-01-01 in unix seconds


def default_db() -> Optional[str]:
    candidates = [os.path.expanduser("~/Library/Group Containers/group.com.apple.usernoted/db2/db")]
    try:  # macOS 14 and older kept it under DARWIN_USER_DIR
        d = subprocess.run(["getconf", "DARWIN_USER_DIR"], capture_output=True, text=True, timeout=2).stdout.strip()
        if d:
            candidates.append(os.path.join(d, "com.apple.notificationcenter", "db2", "db"))
    except Exception:
        pass
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def check(macos_db: Optional[str] = None, **_):
    path = macos_db or default_db()
    if not path:
        return False, "Notification Center database not found"
    try:
        src = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        src.execute("SELECT 1 FROM record LIMIT 1").fetchall()
        src.close()
    except sqlite3.Error:
        return False, ("can't read it: System Settings > Privacy & Security > Full Disk Access,"
                       " add your terminal app, then restart the terminal")
    return True, "reading " + path


def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def parse_record(rec_id: int, identifier: Optional[str], data: bytes, delivered: Optional[float]) -> Optional[dict]:
    try:
        pl = plistlib.loads(data)
    except Exception:
        return None
    if not isinstance(pl, dict):
        return None
    req = pl.get("req") if isinstance(pl.get("req"), dict) else {}
    app = _text(pl.get("app")) or (identifier or "unknown")
    title, subt, body = _text(req.get("titl")), _text(req.get("subt")), _text(req.get("body"))
    when = delivered if delivered is not None else pl.get("date")
    at = float(when) + MAC_EPOCH if isinstance(when, (int, float)) else 0.0
    # Messages/Slack/Discord put the person or channel in the title, the message in the body.
    sender = title
    # The conversation id when the app sets one (Messages, Slack, Mail threads), else the title.
    tid = next((req.get(k) for k in ("thre", "thread", "srce") if isinstance(req.get(k), str) and req.get(k)), "")
    head = " · ".join(x for x in (title, subt) if x)
    return {
        "source": NAME,
        "ext_id": str(pl.get("uuid") or rec_id) if not isinstance(pl.get("uuid"), bytes) else pl["uuid"].hex(),
        "app": app,
        "app_name": deeplinks.app_name(app),
        "thread": app + "|t:" + tid if tid else app + "|" + (title or subt or "?"),
        "sender": sender,
        "title": subt if subt and subt != title else "",
        "body": body or head,
        "at": at,
        "url": deeplinks.default_link(app),
    }


def collect(store, macos_db: Optional[str] = None, **_) -> int:
    path = macos_db or default_db()
    if not path:
        return 0
    try:
        src = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise RuntimeError("can't open %s (grant Full Disk Access?): %s" % (path, e))
    try:
        mark = int(store.get_meta("macos.rec_id", "0"))
        rows = src.execute(
            "SELECT r.rec_id, a.identifier, r.data, r.delivered_date FROM record r"
            " LEFT JOIN app a ON a.app_id = r.app_id WHERE r.rec_id > ? ORDER BY r.rec_id", (mark,)).fetchall()
    except sqlite3.Error as e:
        raise RuntimeError("can't read %s (grant Full Disk Access?): %s" % (path, e))
    finally:
        src.close()
    n = 0
    for rec_id, ident, data, delivered in rows:
        item = parse_record(rec_id, ident, data, delivered)
        if item and store.upsert(item):
            n += 1
        mark = max(mark, rec_id)
    store.set_meta("macos.rec_id", str(mark))
    return n
