"""Unread mail over IMAP (Gmail with an app password, or any IMAP server).

Env: PILE_IMAP_USER, PILE_IMAP_PASSWORD, PILE_IMAP_HOST (default imap.gmail.com),
PILE_IMAP_FOLDER (default INBOX), PILE_IMAP_NAME (a label, e.g. "work").
More accounts: the same with a number, PILE_IMAP_2_USER, PILE_IMAP_2_PASSWORD, PILE_IMAP_2_NAME ...
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import os
import re
from typing import Optional
from urllib.parse import quote

NAME = "imap"
PLATFORMS = ("any",)
NEEDS = "an IMAP login (Gmail: an app password)"
ORDER = 45
MAX = 200


def accounts(env=None) -> list:
    """Every configured account. PILE_IMAP_USER/PASSWORD/HOST/FOLDER/NAME is account 1;
    PILE_IMAP_2_USER ... adds more (PILE_IMAP_1_... also works and wins over the plain names)."""
    env = os.environ if env is None else env
    keys = {"1"} | {m.group(1) for k in env for m in [re.match(r"PILE_IMAP_(\d+)_USER$", k)] if m}
    out = []
    for k in sorted(keys, key=int):
        get = lambda f, d=None: env.get("PILE_IMAP_%s_%s" % (k, f)) or (env.get("PILE_IMAP_" + f) if k == "1" else None) or d
        user, password = get("USER"), get("PASSWORD")
        if not (user and password):
            continue
        out.append({"key": k, "user": user, "password": password, "host": get("HOST", "imap.gmail.com"),
                    "folder": get("FOLDER", "INBOX"), "name": get("NAME", user)})
    return out


def available() -> bool:
    return bool(accounts())


def source_for(acct: dict) -> str:
    return "imap:" + acct["name"]


def check(**_):
    accts = accounts()
    if accts:
        return True, ", ".join("%s (%s at %s)" % (a["name"], a["user"], a["host"]) if a["name"] != a["user"]
                               else "%s at %s" % (a["user"], a["host"]) for a in accts)
    return False, ("Gmail: turn on 2-Step Verification, then make an app password at myaccount.google.com/apppasswords\n"
                   "                then: export PILE_IMAP_USER=you@gmail.com PILE_IMAP_PASSWORD='the 16 letters'"
                   " (other servers: also PILE_IMAP_HOST; Outlook/Microsoft 365 need OAuth, not supported yet)")


def _decode(v: Optional[str]) -> str:
    if not v:
        return ""
    return str(email.header.make_header(email.header.decode_header(v))).strip()


def _snippet(msg) -> str:
    part = None
    for p in msg.walk() if msg.is_multipart() else [msg]:
        if p.get_content_type() == "text/plain" and not p.get_filename():
            part = p
            break
    if part is None:
        for p in msg.walk():
            if p.get_content_type() == "text/html":
                part = p
                break
    if part is None:
        return ""
    raw = part.get_payload(decode=True) or b""
    text = raw.decode(part.get_content_charset() or "utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:300]


def link_for(message_id: str, host: str, user: str = "") -> str:
    mid = message_id.strip().strip("<>")
    if "gmail" in host:
        return ("https://mail.google.com/mail/u/%s#search/rfc822msgid:" % (("?authuser=" + quote(user, safe="@.")) if user else "0/")
                + quote(mid, safe="@."))
    return "message://" + quote("<" + mid + ">", safe="@.")


def gmail_thread_link(thrid: int, user: str = "") -> str:
    # authuser picks the right signed-in account however many you have open.
    return "https://mail.google.com/mail/u/%s#all/%x" % (("?authuser=" + quote(user, safe="@.")) if user else "0/", int(thrid))


def fetch_meta(head: bytes) -> dict:
    """The FETCH response line: Gmail's thread id and the message's flags."""
    head = head.decode("ascii", "replace") if isinstance(head, bytes) else head
    thr = re.search(r"X-GM-THRID (\d+)", head)
    flags = re.search(r"FLAGS \(([^)]*)\)", head)
    return {"thrid": int(thr.group(1)) if thr else None, "answered": bool(flags and "\\Answered" in flags.group(1))}


def me() -> set:
    mine = {a.strip().lower() for a in os.environ.get("PILE_MY_EMAILS", "").split(",") if a.strip()}
    return mine | {a["user"].lower() for a in accounts()}


def to_item(raw: bytes, host: str, meta: dict = None, mine: set = None, acct: dict = None) -> dict:
    meta = meta or {}
    msg = email.message_from_bytes(raw)
    name, addr = email.utils.parseaddr(_decode(msg.get("From")))
    mid = (msg.get("Message-ID") or "").strip()
    try:
        at = email.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:
        at = 0.0
    gmail = "gmail" in host
    from .. import mailtasks
    person = is_person(msg)
    mine = mine if mine is not None else me()
    to = {a.lower() for _, a in email.utils.getaddresses([_decode(x) for x in msg.get_all("To", [])])}
    direct = (not mine) or bool(to & mine)
    subject, body = _decode(msg.get("Subject")), _snippet(msg)
    task = mailtasks.is_task(person, direct, subject + " " + body, meta.get("answered", False))
    thrid = meta.get("thrid")
    user = acct["user"] if acct else ""
    many = len(accounts()) > 1
    return {
        "source": source_for(acct) if acct else NAME,
        "ext_id": mid or (addr + str(at)),
        "app": "gmail" if gmail else "imap",
        "app_name": ("Gmail" if gmail else "Mail") + (" · " + acct["name"] if acct and many else ""),
        "thread": ("gmail|thr:%x" % thrid) if thrid else "mail|" + (addr or name),
        "sender": name or addr,
        "title": subject,
        "body": body,
        "at": at,
        "url": gmail_thread_link(thrid, user) if thrid else (link_for(mid, host, user) if mid else ""),
        "tag": "task" if task else (None if person else "sift"),
    }


def _fetch(conn, num, host):
    what = "(X-GM-THRID FLAGS BODY.PEEK[])" if "gmail" in host else "(FLAGS BODY.PEEK[])"
    _, parts = conn.fetch(num, what)
    part = next((p for p in parts if isinstance(p, tuple)), None)
    if not part:
        return None
    return part[1], fetch_meta(part[0])


def _session(acct):
    conn = imaplib.IMAP4_SSL(acct["host"], timeout=30)
    conn.login(acct["user"], acct["password"])
    conn.select(acct["folder"], readonly=True)
    return conn


def _close(conn):
    try:
        conn.logout()
    except Exception:
        pass


def collect_account(store, acct) -> int:
    conn = _session(acct)
    try:
        _, data = conn.search(None, "UNSEEN")
        nums = (data[0] or b"").split()[-MAX:]  # the newest MAX unread
        live, n = [], 0
        for num in nums:
            got = _fetch(conn, num, acct["host"])
            if not got:
                continue
            item = to_item(got[0], acct["host"], got[1], acct=acct)
            live.append(item["ext_id"])
            if store.upsert(item):
                n += 1
        if len(nums) < MAX:  # only reconcile when we saw every unread message
            store.close_missing(source_for(acct), live)
            if acct["key"] == "1":
                store.close_missing(NAME, [])  # items from before accounts had names
        return n
    finally:
        _close(conn)


def collect(store, **_) -> int:
    total, errors = 0, []
    for acct in accounts():
        try:
            total += collect_account(store, acct)
        except Exception as e:  # one bad account doesn't stop the others
            errors.append("%s: %s" % (acct["name"], e))
    if errors and not total:
        raise RuntimeError("; ".join(errors))
    return total


def doctor_lines() -> list:
    """Try each account's login (passwords never printed)."""
    out = []
    for acct in accounts():
        try:
            _close(_session(acct))
            out.append((True, "%s: %s at %s, logged in" % (acct["name"], acct["user"], acct["host"])))
        except Exception as e:
            out.append((False, "%s: %s at %s, login failed: %s" % (acct["name"], acct["user"], acct["host"], e)))
    return out


BULK = re.compile(r"(no-?reply|do-?not-?reply|notifications?@|mailer-daemon|bounce)", re.I)


def is_person(msg) -> bool:
    """Real people, not lists, receipts or robots."""
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return False
    if (msg.get("Precedence") or "").lower() in ("bulk", "list", "junk"):
        return False
    if (msg.get("Auto-Submitted") or "no").lower() != "no":
        return False
    return not BULK.search(msg.get("From") or "")


def backfill(store, days: float = 30, **_) -> int:
    """Mail from people in the last N days that you never answered (no \\Answered flag)."""
    import time as _time
    since = _time.strftime("%d-%b-%Y", _time.localtime(_time.time() - days * 86400))
    n = 0
    for acct in accounts():
        conn = _session(acct)
        try:
            _, data = conn.search(None, "SINCE", since, "UNANSWERED", "NOT", "FROM", '"%s"' % acct["user"])
            for num in (data[0] or b"").split()[-200:]:
                got = _fetch(conn, num, acct["host"])
                if not got or not is_person(email.message_from_bytes(got[0])):
                    continue
                item = to_item(got[0], acct["host"], got[1], acct=acct)
                item["tag"] = "task" if item["tag"] == "task" else "unanswered"
                if store.upsert(item):
                    n += 1
        finally:
            _close(conn)
    return n
