"""The verbs the web UI and the MCP server share."""
from __future__ import annotations

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
    n = store.set_state(ids, "snoozed", t)
    return {"snoozed": n, "until": datetime.fromtimestamp(t).isoformat(timespec="minutes")}


def done(store, ids) -> dict:
    return {"done": store.set_state(ids, "done")}


def reopen(store, ids) -> dict:
    return {"reopened": store.set_state(ids, "new")}


def open_item(store, item_id) -> dict:
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    did = deeplinks.open_link(item["link"])
    return {"id": item["id"], "link": item["link"], "learned": item["link_learned"], "result": did}


def fix_link(store, item_id, url) -> dict:
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    rule = rules.learn_link(store, item, url)
    return {"rule": rule, "now_opens": store.get(item_id)["link"],
            "scope": ("every %s item" % item["app_name"]) if rule["sender"] is None
            else ("%s in %s" % (item["sender"] or item["title"], item["app_name"]))}


def teach_label(store, item_id, label, scope="sender") -> dict:
    """'not important' (noise) / 'always important' (needs-you) for this sender, or the whole app."""
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    sender = None if scope == "app" else (item["thread"] or item["sender"])
    return {"rule": rules.add(store, "label", label, app=item["app"], sender=sender,
                              note=(item["sender"] or item["title"]) if sender else item["app_name"])}


def mute_app(store, item_id) -> dict:
    item = store.get(item_id)
    if not item:
        raise KeyError("no item %s" % item_id)
    return {"rule": rules.add(store, "mute", app=item["app"], note=item["app_name"])}
