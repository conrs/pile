"""The verbs the web UI and the MCP server share."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Union

from . import deeplinks, rules


def parse_until(until: Union[str, int, float], now: float = None) -> float:
    """'1h', '30m', '2d', 'tomorrow' (9am local), 'tonight' (6pm), an ISO time, or unix seconds."""
    now = now or time.time()
    if isinstance(until, (int, float)):
        return float(until)
    s = str(until).strip().lower()
    if s and s[:-1].isdigit() and s[-1] in "mhd":
        return now + int(s[:-1]) * {"m": 60, "h": 3600, "d": 86400}[s[-1]]
    local = datetime.fromtimestamp(now)
    if s == "tomorrow":
        return (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    if s == "tonight":
        t = local.replace(hour=18, minute=0, second=0, microsecond=0)
        return (t if t > local else t + timedelta(days=1)).timestamp()
    if s.replace(".", "", 1).isdigit():
        return float(s)
    return datetime.fromisoformat(str(until).strip().replace("Z", "+00:00")).timestamp()


def snooze(store, ids, until) -> dict:
    t = parse_until(until)
    with store.recording("snooze", ids, until=t):
        n = store.set_state(ids, "snoozed", t)
    return {"snoozed": n, "until": datetime.fromtimestamp(t).isoformat(timespec="minutes")}


def done(store, ids) -> dict:
    with store.recording("done", ids):
        n = store.set_state(ids, "done")
    return {"done": n}


def reopen(store, ids) -> dict:
    with store.recording("reopen", ids):
        n = store.set_state(ids, "new")
    return {"reopened": n}


def open_item(store, item_id) -> dict:
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    did = deeplinks.open_link(item["link"])
    store.log("open", [item["id"]], link=item["link"], learned=item["link_learned"])
    return {"id": item["id"], "link": item["link"], "learned": item["link_learned"], "result": did}


def fix_link(store, item_id, url) -> dict:
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    with store.recording("fix_link", [item["id"]], url=url):
        rule = rules.learn_link(store, item, url)
    fields = sorted(set(re.findall(r"\{(\w+)", rule["value"])))
    scope = ("every %s item, filling in the %s" % (item["app_name"], " and ".join(f.replace("ext_id", "id") for f in fields))
             if rule["sender"] is None else "%s in %s" % (item["sender"] or item["title"], item["app_name"]))
    return {"rule": rule, "now_opens": store.get(item_id)["link"], "scope": scope}


def teach_label(store, item_id, label, scope="sender") -> dict:
    """'not important' (noise) / 'always important' (needs-you) for this sender, or the whole app."""
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    sender = None if scope == "app" else (item["thread"] or item["sender"])
    with store.recording("label", [item["id"]], label=label, scope=scope):
        rule = rules.add(store, "label", label, app=item["app"], sender=sender,
                         note=(item["sender"] or item["title"]) if sender else item["app_name"])
    return {"rule": rule}


def mute_app(store, item_id) -> dict:
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    with store.recording("mute", [item["id"]], app=item["app"]):
        rule = rules.add(store, "mute", app=item["app"], note=item["app_name"])
    return {"rule": rule}


def group(store, item_id, with_id) -> dict:
    """Teach pile that two rows are one conversation: `with_id`'s thread joins `item_id`'s."""
    a, b = store.get(item_id), store.get(with_id)
    if not a or not b:
        raise KeyError("no such item")
    if a["thread"] == b["thread"]:
        return {"rule": None, "thread": a["thread"]}
    with store.recording("group", [item_id, with_id]):
        rule = rules.add(store, "group", a["thread"], app=b["app"], sender=b["thread"],
                         note="%s + %s" % (a["sender"] or a["title"], b["sender"] or b["title"]))
    return {"rule": rule, "thread": a["thread"]}


def ungroup(store, item_id) -> dict:
    """Forget every learned grouping into this item's thread."""
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    with store.recording("ungroup", [item_id]):
        n = store.db.execute("DELETE FROM rules WHERE kind='group' AND (value=? OR sender=?)",
                             (item["thread"], item.get("raw_thread") or item["thread"])).rowcount
    return {"forgot": n}


def undo(store, steps=1) -> dict:
    return store.undo(steps)


def redo(store, steps=1) -> dict:
    return store.redo(steps)


def teach_labels(store, ids, label, scope="sender") -> dict:
    """teach_label for several rows, as one undoable action."""
    ids = [int(i) for i in ids]
    out = []
    with store.recording("label", ids, label=label, scope=scope):
        for i in ids:
            item = store.get(i)
            if not item:
                raise KeyError("no item %s" % i)
            sender = None if scope == "app" else (item["thread"] or item["sender"])
            out.append(rules.add(store, "label", label, app=item["app"], sender=sender,
                                 note=(item["sender"] or item["title"]) if sender else item["app_name"]))
    return {"rules": out}


def mute_apps(store, ids) -> dict:
    ids = [int(i) for i in ids]
    out, seen = [], set()
    with store.recording("mute", ids):
        for i in ids:
            item = store.get(i)
            if item and item["app"] not in seen:
                seen.add(item["app"])
                out.append(rules.add(store, "mute", app=item["app"], note=item["app_name"]))
    return {"rules": out}


def combine(store, ids) -> dict:
    """Fold several rows into one conversation: the newest row's thread. One undoable action."""
    items = [store.get(int(i)) for i in ids]
    items = [i for i in items if i]
    if len(items) < 2:
        raise ValueError("select at least two rows")
    target = max(items, key=lambda i: i["at"])
    out = []
    with store.recording("combine", [i["id"] for i in items]):
        for it in items:
            if it["thread"] != target["thread"]:
                out.append(rules.add(store, "group", target["thread"], app=it["app"], sender=it["thread"],
                                     note="%s + %s" % (target["sender"] or target["title"], it["sender"] or it["title"])))
    return {"thread": target["thread"], "rules": out}
