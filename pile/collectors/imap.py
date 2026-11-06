"""Unread mail over IMAP (Gmail with an app password, or any IMAP server).

Env: PILE_IMAP_USER, PILE_IMAP_PASSWORD, PILE_IMAP_HOST (default imap.gmail.com),
PILE_IMAP_FOLDER (default INBOX).
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
MAX = 50


def available() -> bool:
    return bool(os.environ.get("PILE_IMAP_USER") and os.environ.get("PILE_IMAP_PASSWORD"))


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


def link_for(message_id: str, host: str) -> str:
    mid = message_id.strip().strip("<>")
    if "gmail" in host:
        return "https://mail.google.com/mail/u/0/#search/rfc822msgid:" + quote(mid, safe="@.")
    return "message://" + quote("<" + mid + ">", safe="@.")


def to_item(raw: bytes, host: str) -> dict:
    msg = email.message_from_bytes(raw)
    name, addr = email.utils.parseaddr(_decode(msg.get("From")))
    mid = (msg.get("Message-ID") or "").strip()
    try:
        at = email.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:
        at = 0.0
    gmail = "gmail" in host
    return {
        "source": NAME,
        "ext_id": mid or (addr + str(at)),
        "app": "gmail" if gmail else "imap",
        "app_name": "Gmail" if gmail else "Mail",
        "thread": "mail|" + (addr or name),
        "sender": name or addr,
        "title": _decode(msg.get("Subject")),
        "body": _snippet(msg),
        "at": at,
        "url": link_for(mid, host) if mid else "",
    }


def collect(store) -> int:
    if not available():
        return 0
    host = os.environ.get("PILE_IMAP_HOST", "imap.gmail.com")
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(os.environ["PILE_IMAP_USER"], os.environ["PILE_IMAP_PASSWORD"])
        conn.select(os.environ.get("PILE_IMAP_FOLDER", "INBOX"), readonly=True)
        _, data = conn.search(None, "UNSEEN")
        nums = (data[0] or b"").split()[-MAX:]
        live, n = [], 0
        for num in nums:
            _, parts = conn.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if not raw:
                continue
            item = to_item(raw, host)
            live.append(item["ext_id"])
            if store.upsert(item):
                n += 1
        if len(nums) < MAX:  # only reconcile when we saw every unread message
            store.close_missing(NAME, live)
        return n
    finally:
        try:
            conn.logout()
        except Exception:
            pass
