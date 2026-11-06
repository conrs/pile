"""Apple Mail on macOS: every account Mail.app has (iCloud, Gmail, Exchange...), read-only, no setup.

Reads Mail's own index (~/Library/Mail/V*/MailData/Envelope Index) with the same Full Disk Access as
the Notification Center collector. Unread mail from people lands in the inbox (as a "task" when it asks
you for something); newsletters, receipts and other robots go to Sift, for after inbox zero.
"""
from __future__ import annotations

import glob
import os
import sqlite3
import time
from typing import Optional
from urllib.parse import quote

from .. import mailtasks

NAME = "mailapp"
PLATFORMS = ("darwin",)
NEEDS = "Full Disk Access for your terminal, and Mail.app set up"
ORDER = 20
PATH_OPT = "mail_db"
MAC_EPOCH = 978307200
APP = "com.apple.mail"
DAYS = 30
ANSWERED = 1 << 2  # Mail's message flags


def default_db() -> Optional[str]:
    found = glob.glob(os.path.expanduser("~/Library/Mail/V*/MailData/Envelope Index"))
    found.sort(key=lambda p: int("".join(c for c in p.split("/Mail/V")[1].split("/")[0] if c.isdigit()) or 0))
    return found[-1] if found else None


def _open(path):
    db = sqlite3.connect("file:%s?mode=ro&immutable=1" % quote(path), uri=True)
    db.row_factory = sqlite3.Row
    return db


def _cols(db, table):
    return {r["name"] for r in db.execute("PRAGMA table_info(%s)" % table)}


def check(mail_db: Optional[str] = None, **_):
    path = mail_db or default_db()
    if not path:
        return False, "no Mail.app index (set up Mail.app, or use the imap collector)"
    try:
        db = _open(path)
        need = {"messages": {"sender", "subject", "date_received", "read", "mailbox"}, "addresses": {"address"},
                "subjects": {"subject"}, "mailboxes": {"url"}}
        for table, cols in need.items():
            missing = cols - _cols(db, table)
            if missing:
                return False, "this Mail version's index is missing %s.%s; please file an issue" % (table, ",".join(missing))
        db.close()
    except sqlite3.Error:
        return False, "can't read Mail's index: give your terminal Full Disk Access"
    return True, "unread mail from every Mail.app account"


def _when(v) -> float:
    v = float(v or 0)
    return v + MAC_EPOCH if 0 < v < 1.2e9 else v


def my_addresses(db, mcols) -> set:
    """Your own addresses: PILE_MY_EMAILS, else the addresses most of your mail is sent To."""
    mine = {a.strip().lower() for a in os.environ.get("PILE_MY_EMAILS", "").split(",") if a.strip()}
    if mine or "type" not in _cols(db, "recipients"):
        return mine
    rows = db.execute("SELECT a.address, COUNT(*) n FROM recipients r JOIN addresses a ON a.ROWID = r.address"
                      " WHERE r.type = 0 GROUP BY a.address ORDER BY n DESC LIMIT 3").fetchall()
    total = db.execute("SELECT COUNT(*) FROM recipients WHERE type = 0").fetchone()[0] or 1
    return {r["address"].lower() for r in rows if r["n"] >= 0.15 * total}


def rows(path: str, days: float = DAYS, now: Optional[float] = None) -> list:
    now = now or time.time()
    db = _open(path)
    try:
        m = _cols(db, "messages")
        opt = lambda col, expr, default="NULL": (expr if col in m else default)
        summary = "(SELECT s.summary FROM summaries s WHERE s.ROWID = m.summary)" if "summary" in m and "summaries" in {
            r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} else "''"
        msgid = ("(SELECT g.message_id_header FROM message_global_data g WHERE g.ROWID = m.global_message_id)"
                 if "global_message_id" in m else "NULL")
        cutoff = now - days * 86400
        q = ("SELECT m.ROWID id, a.address, a.comment name, s.subject, " + summary + " summary, " + msgid + " msgid,"
             " m.date_received, m.read, " + opt("flags", "m.flags", "0") + " flags,"
             " " + opt("list_id_hash", "m.list_id_hash", "0") + " list_id,"
             " " + opt("unsubscribe_type", "m.unsubscribe_type", "0") + " unsub,"
             " " + opt("automated_conversation", "m.automated_conversation", "0") + " automated,"
             " " + opt("deleted", "m.deleted", "0") + " deleted, mb.url mailbox"
             " FROM messages m JOIN addresses a ON a.ROWID = m.sender JOIN subjects s ON s.ROWID = m.subject"
             " JOIN mailboxes mb ON mb.ROWID = m.mailbox"
             " WHERE m.read = 0 AND (m.date_received >= ? OR m.date_received >= ?)")
        out = [dict(r) for r in db.execute(q, (cutoff, cutoff - MAC_EPOCH))]
        out = [r for r in out if not r["deleted"] and "inbox" in (r["mailbox"] or "").lower()]
        mine = my_addresses(db, m)
        if "type" in _cols(db, "recipients") and out:
            marks = ",".join("?" * len(out))
            to = {}
            for r in db.execute("SELECT r.message, a.address FROM recipients r JOIN addresses a ON a.ROWID = r.address"
                                " WHERE r.type = 0 AND r.message IN (%s)" % marks, [r["id"] for r in out]):
                to.setdefault(r["message"], set()).add(r["address"].lower())
            for r in out:
                r["direct"] = (not mine) or bool(to.get(r["id"], set()) & mine)
        else:
            for r in out:
                r["direct"] = True
        return out
    finally:
        db.close()


def to_item(r: dict) -> dict:
    person = not (r["list_id"] or r["unsub"] or r["automated"] or mailtasks.is_bulk_sender(r["address"]))
    text = "%s %s" % (r["subject"] or "", r["summary"] or "")
    task = mailtasks.is_task(person, r["direct"], text, bool((r["flags"] or 0) & ANSWERED))
    mid = (r["msgid"] or "").strip().strip("<>")
    return {"source": NAME, "ext_id": "mail:%s" % r["id"], "app": APP, "app_name": "Mail",
            "thread": "mail|" + (r["address"] or "").lower(), "sender": r["name"] or r["address"] or "",
            "title": r["subject"] or "", "body": (r["summary"] or "").strip()[:300], "at": _when(r["date_received"]),
            "url": "message://" + quote("<%s>" % mid, safe="@.") if mid else "bundle:" + APP,
            "tag": "task" if task else (None if person else "sift")}


def collect(store, mail_db: Optional[str] = None, **_) -> int:
    path = mail_db or default_db()
    if not path:
        return 0
    found = rows(path)
    n = 0
    for r in found:
        if store.upsert(to_item(r)):
            n += 1
    store.close_missing(NAME, ["mail:%s" % r["id"] for r in found])  # read in Mail: clear it here too
    return n
