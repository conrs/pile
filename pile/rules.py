"""What pile learns from you: link fixes, importance, muted apps.

A rule matches on app (bundle id; NULL = any) and sender (a person, channel or
thread key; NULL = anyone). The most specific match wins, then the newest.

Link fixes generalise: if the link you paste contains the item's sender, title
or thread (raw, URL-encoded or slugified), those parts become placeholders and
the rule covers the whole app. Otherwise it covers just that sender/thread.
"""
from __future__ import annotations

import re
import time
from typing import Optional, Tuple
from urllib.parse import quote

from . import deeplinks

KINDS = ("link", "label", "mute", "group")
LABELS = ("needs-you", "fyi", "noise")
PLACEHOLDER_FIELDS = ("sender", "title", "ext_id")
_PH = re.compile(r"\{(sender|title|ext_id)(?::(q|slug))?\}")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _encode(value: str, enc: Optional[str]) -> str:
    if enc == "q":
        return quote(value, safe="")
    if enc == "slug":
        return slug(value)
    return value


def generalise(url: str, item: dict) -> Tuple[str, bool]:
    """Turn a pasted URL into a template using the item's fields. Returns (template, generic?)."""
    template, generic = url, False
    seen = set()
    fields = sorted(((f, (item.get(f) or "").strip()) for f in PLACEHOLDER_FIELDS),
                    key=lambda fv: -len(fv[1]))
    for field, value in fields:
        if len(value) < 3 or value in seen:
            continue
        seen.add(value)
        for enc in ("q", "slug"):
            needle = _encode(value, enc)
            if len(needle) < 3:
                continue
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])",
                             re.I if enc == "slug" else 0)
            # Leave the scheme and host alone: only the path/query/fragment generalise.
            head, sep, rest = template.partition("://")
            host, slash, tail = rest.partition("/") if sep else ("", "", template)
            new_tail, n = pat.subn("{%s:%s}" % (field, enc), tail)
            if n:
                template = head + sep + host + slash + new_tail if sep else new_tail
                generic = True
                break
    return template, generic


def normalise_target(pasted: str) -> str:
    pasted = pasted.strip()
    if "://" not in pasted and ":" not in pasted and re.fullmatch(r"[A-Za-z0-9.\-]+\.[A-Za-z0-9.\-]+", pasted):
        return "bundle:" + pasted  # a bare bundle id: open that app
    return pasted


def render(template: str, item: dict) -> Optional[str]:
    missing = []

    def sub(m):
        v = (item.get(m.group(1)) or "").strip()
        if not v:
            missing.append(m.group(1))
        return _encode(v, m.group(2))
    out = _PH.sub(sub, template)
    return None if missing else out


def add(store, kind: str, value: str = "", app: Optional[str] = None, sender: Optional[str] = None,
        note: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError("kind must be one of " + ", ".join(KINDS))
    if kind == "label" and value not in LABELS:
        raise ValueError("label must be one of " + ", ".join(LABELS))
    if kind == "mute" and not app:
        raise ValueError("mute needs an app")
    if kind == "group" and not (sender and value):
        raise ValueError("group needs match.sender (the thread to fold in) and value (the thread it joins)")
    # One rule per (kind, app, sender): a new answer replaces the old one.
    store.db.execute("DELETE FROM rules WHERE kind=? AND app IS ? AND sender IS ?", (kind, app, sender))
    cur = store.db.execute("INSERT INTO rules(kind, app, sender, value, note, created) VALUES(?,?,?,?,?,?)",
                           (kind, app, sender, value, note, time.time()))
    if kind == "mute":
        store.db.execute("UPDATE items SET state='done' WHERE app=? AND state!='done'", (app,))
    if kind == "label":
        q = "UPDATE items SET label=?, label_by='rule' WHERE state!='done'"
        args = [value]
        if app:
            q += " AND app=?"; args.append(app)
        if sender:
            q += " AND (sender=? OR thread=?)"; args += [sender, sender]
        store.db.execute(q, args)
    return get(store, cur.lastrowid)


def get(store, rule_id: int) -> Optional[dict]:
    r = store.db.execute("SELECT * FROM rules WHERE id=?", (int(rule_id),)).fetchone()
    return dict(r) if r else None


def all_rules(store) -> list:
    out = []
    for r in store.db.execute("SELECT * FROM rules ORDER BY created DESC, id DESC"):
        r = dict(r)
        if r["app"]:
            seen = store.db.execute("SELECT app_name FROM items WHERE app=? LIMIT 1", (r["app"],)).fetchone()
            r["app_name"] = seen["app_name"] if seen else deeplinks.app_name(r["app"])
        out.append(r)
    return out


def delete(store, rule_id: int) -> bool:
    return store.db.execute("DELETE FROM rules WHERE id=?", (int(rule_id),)).rowcount > 0


def _matching(store, kind: str, item: dict) -> list:
    rows = store.db.execute(
        "SELECT * FROM rules WHERE kind=? AND (app IS NULL OR app=?)"
        " AND (sender IS NULL OR sender=? OR sender=?)"
        " ORDER BY (sender IS NOT NULL) DESC, (app IS NOT NULL) DESC, created DESC, id DESC",
        (kind, item.get("app"), item.get("sender") or "", item.get("thread") or ""))
    return [dict(r) for r in rows]


def thread_for(store, item: dict) -> str:
    """The item's conversation after learned groupings (followed a few hops)."""
    thread = item.get("thread") or ""
    for _ in range(4):
        row = store.db.execute("SELECT value FROM rules WHERE kind='group' AND (app IS NULL OR app=?) AND sender=?"
                               " ORDER BY id DESC LIMIT 1", (item.get("app"), thread)).fetchone()
        if not row or row["value"] == thread:
            break
        thread = row["value"]
    return thread


def learn_link(store, item: dict, pasted: str) -> dict:
    target = normalise_target(pasted)
    if not target:
        raise ValueError("empty link")
    template, generic = generalise(target, item)
    sender = None if generic else (item.get("thread") or item.get("sender") or None)
    note = "learned from: " + (item.get("sender") or item.get("title") or "")
    return add(store, "link", template, app=item["app"], sender=sender, note=note)


def resolve_link(store, item: dict) -> Tuple[str, bool]:
    for rule in _matching(store, "link", item):
        link = render(rule["value"], item)
        if link:
            return link, True
    return item.get("url") or deeplinks.default_link(item.get("app", "")), False


def label_for(store, item: dict) -> Optional[str]:
    rows = _matching(store, "label", item)
    return rows[0]["value"] if rows else None


def is_muted(store, app: str) -> bool:
    return store.db.execute("SELECT 1 FROM rules WHERE kind='mute' AND app=?", (app,)).fetchone() is not None
