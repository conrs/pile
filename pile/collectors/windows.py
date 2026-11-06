"""Windows notifications (Action Center), read-only.

Reads %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db. No admin needed.
"""
from __future__ import annotations

import os
import sqlite3
import xml.etree.ElementTree as ET
from typing import Optional

from .. import deeplinks

NAME = "windows"
PLATFORMS = ("windows",)
NEEDS = "nothing (reads your own notification database)"
ORDER = 11
PATH_OPT = "windows_db"
FILETIME_EPOCH = 11644473600  # 1601-01-01 to 1970-01-01, seconds

# Windows app ids (AUMIDs) -> pile app keys, so deep links and colours match the other platforms.
KNOWN = {
    "slack": "com.tinyspeck.slackmacgap", "discord": "com.hnc.Discord", "teams": "com.microsoft.teams2",
    "whatsapp": "net.whatsapp.WhatsApp", "telegram": "ru.keepcoder.Telegram", "linear": "com.linear",
    "outlook": "outlook", "zoom": "us.zoom.xos", "spotify": "com.spotify.client",
}


def default_db() -> Optional[str]:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    p = os.path.join(base, "Microsoft", "Windows", "Notifications", "wpndatabase.db")
    return p if os.path.exists(p) else None


def check(windows_db: Optional[str] = None, **_):
    path = windows_db or default_db()
    if not path:
        return False, "wpndatabase.db not found (turn on notifications in Settings > System)"
    return True, "reading " + path


def filetime_to_unix(ft) -> float:
    return int(ft) / 1e7 - FILETIME_EPOCH if ft else 0.0


def app_key(aumid: str) -> str:
    low = (aumid or "").lower()
    for word, key in KNOWN.items():
        if word in low:
            return key
    return "win:" + (aumid or "unknown")


def parse_payload(payload) -> dict:
    """Toast XML -> texts and an optional protocol launch link."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return {"texts": [], "launch": ""}
    texts = [(t.text or "").strip() for t in root.iter("text") if (t.text or "").strip()]
    launch = root.get("launch") or ""
    proto = root.get("activationType") == "protocol" or "://" in launch
    return {"texts": texts, "launch": launch if proto else ""}


def to_item(nid, aumid, payload, arrival) -> Optional[dict]:
    p = parse_payload(payload)
    if not p["texts"]:
        return None
    key = app_key(aumid)
    name = deeplinks.app_name(key) if not key.startswith("win:") else (aumid.split("!")[0].split("_")[0].split(".")[-1] or aumid)
    texts = p["texts"]
    sender, body = texts[0], " ".join(texts[1:])
    link = p["launch"] or (deeplinks.default_link(key) if not key.startswith("win:") else "shell:AppsFolder\\" + aumid)
    return {"source": NAME, "ext_id": str(nid), "app": key, "app_name": name, "thread": key + "|" + sender,
            "sender": sender, "title": "", "body": body or sender, "at": filetime_to_unix(arrival), "url": link}


def collect(store, windows_db: Optional[str] = None, **_) -> int:
    path = windows_db or default_db()
    if not path:
        return 0
    src = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    try:
        mark = int(store.get_meta("windows.id", "0"))
        rows = src.execute(
            "SELECT n.Id, h.PrimaryId, n.Payload, n.ArrivalTime FROM Notification n"
            " LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId"
            " WHERE n.Id > ? AND n.Type = 'toast' ORDER BY n.Id", (mark,)).fetchall()
    finally:
        src.close()
    n = 0
    for nid, aumid, payload, arrival in rows:
        item = to_item(nid, aumid or "", payload or b"", arrival)
        if item and store.upsert(item):
            n += 1
        mark = max(mark, nid)
    store.set_meta("windows.id", str(mark))
    return n
