"""Messages on macOS: conversations where they spoke last and you never replied.

Backfill only (`pile backfill`): reads ~/Library/Messages/chat.db read-only. Needs the
same Full Disk Access as the Notification Center collector.
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import time
from typing import Optional

NAME = "messages"
PLATFORMS = ("darwin",)
NEEDS = "Full Disk Access for your terminal (runs with `pile backfill`)"
ORDER = 15
PATH_OPT = "messages_db"
MAC_EPOCH = 978307200
APP = "com.apple.MobileSMS"
QUIET = 2 * 3600  # they wrote less than this long ago: not "unanswered" yet


def default_db() -> Optional[str]:
    p = os.path.expanduser("~/Library/Messages/chat.db")
    return p if os.path.exists(p) else None


def _open(path):
    return sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True)


def check(messages_db: Optional[str] = None, **_):
    path = messages_db or default_db()
    if not path:
        return False, "no Messages database"
    try:
        db = _open(path)
        db.execute("SELECT 1 FROM message LIMIT 1").fetchall()
        db.close()
    except sqlite3.Error:
        return False, "can't read chat.db: give your terminal Full Disk Access"
    return True, "unanswered conversations, via `pile backfill`"


def mac_time(v) -> float:
    if not v:
        return 0.0
    v = float(v)
    return (v / 1e9 if v > 1e12 else v) + MAC_EPOCH  # nanoseconds since macOS 10.13, seconds before


def body_text(text, attributed) -> str:
    """Message text; newer macOS leaves `text` NULL and keeps it in an NSAttributedString typedstream."""
    if text:
        return text
    if not attributed:
        return ""
    try:
        blob = bytes(attributed)
        rest = blob.split(b"NSString", 1)[1]
        i = rest.index(b"+") + 1  # the string's type marker
        n = rest[i]
        if n == 0x81:
            n, i = int.from_bytes(rest[i + 1:i + 3], "little"), i + 3
        else:
            i += 1
        return rest[i:i + n].decode("utf-8", "replace")
    except (IndexError, ValueError):
        return ""


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)[-10:]


def contact_names() -> dict:
    """Phone/email -> name from the local Contacts database, if it's readable. Best effort."""
    names = {}
    for path in glob.glob(os.path.expanduser("~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")):
        try:
            db = _open(path)
            people = {r[0]: " ".join(x for x in r[1:] if x) for r in
                      db.execute("SELECT Z_PK, ZFIRSTNAME, ZLASTNAME FROM ZABCDRECORD")}
            for owner, num in db.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if people.get(owner) and num:
                    names[_digits(num)] = people[owner]
            for owner, addr in db.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if people.get(owner) and addr:
                    names[addr.lower()] = people[owner]
            db.close()
        except sqlite3.Error:
            continue
    return names


def name_for(handle: str, names: dict) -> str:
    if not handle:
        return "Unknown"
    return names.get(handle.lower()) or names.get(_digits(handle)) or handle


def unanswered(path: str, days: float, now: Optional[float] = None) -> list:
    """(chat, last message) pairs where the last real message is theirs."""
    now = now or time.time()
    db = _open(path)
    db.row_factory = sqlite3.Row
    cols = {r["name"] for r in db.execute("PRAGMA table_info(message)")}
    react = " AND COALESCE(m.associated_message_type, 0) = 0" if "associated_message_type" in cols else ""
    attributed = "m.attributedBody" if "attributedBody" in cols else "NULL"
    since = now - days * 86400 - MAC_EPOCH
    biggest = db.execute("SELECT MAX(date) FROM message").fetchone()[0] or 0
    if biggest > 1e12:  # nanoseconds
        since *= 1e9
    rows = db.execute(
        "SELECT c.ROWID chat, c.guid, c.chat_identifier, c.display_name, c.style, m.guid mguid, m.text, "
        + attributed + " attributed, m.is_from_me, m.date, h.id handle,"
        " (SELECT COUNT(*) FROM chat_handle_join chj WHERE chj.chat_id = c.ROWID) members"
        " FROM chat c JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID"
        " JOIN message m ON m.ROWID = cmj.message_id LEFT JOIN handle h ON h.ROWID = m.handle_id"
        " WHERE m.date > ?" + react + " ORDER BY c.ROWID, m.date", (since,)).fetchall()
    db.close()
    last = {}
    for r in rows:
        last[r["chat"]] = r
    return [r for r in last.values() if not r["is_from_me"] and now - mac_time(r["date"]) > QUIET]


def to_item(r, names: dict) -> dict:
    handle = r["handle"] or r["chat_identifier"] or ""
    sender = name_for(handle, names)
    group = r["members"] > 1 or r["style"] == 43
    if group:
        title = r["display_name"] or "Group chat"
        link = "bundle:" + APP
    else:
        title = ""
        link = ("imessage:" if "@" in handle else "sms:") + handle
    return {"source": NAME, "ext_id": "unanswered:" + r["guid"], "app": APP, "app_name": "Messages",
            "thread": APP + "|chat:" + r["guid"], "sender": sender, "title": title,
            "body": body_text(r["text"], r["attributed"]) or "(attachment)", "at": mac_time(r["date"]),
            "url": link, "tag": "unanswered"}


def backfill(store, days: float = 30, messages_db: Optional[str] = None, **_) -> int:
    path = messages_db or default_db()
    if not path:
        return 0
    names = contact_names()
    n = 0
    for r in unanswered(path, days):
        if store.upsert(to_item(r, names)):
            n += 1
    return n
