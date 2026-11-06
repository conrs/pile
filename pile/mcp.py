"""MCP over stdio: newline-delimited JSON-RPC 2.0, no SDK."""
from __future__ import annotations

import json
import sys
from datetime import datetime

from . import __version__, actions, rules

PROTOCOL = "2025-06-18"

IDS = {"type": "array", "items": {"type": "integer"}, "description": "Item ids from list_inbox"}
TOOLS = [
    {"name": "list_inbox", "description": "Notifications and unread items from every source, newest first. "
     "Each has id, app, sender, title, body, when, link, label (needs-you/fyi/noise, if triaged).",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string", "description": "macos, github or imap"},
         "state": {"type": "string", "enum": ["new", "snoozed", "done", "all"], "default": "new"},
         "label": {"type": "string", "enum": list(rules.LABELS)},
         "limit": {"type": "integer", "default": 50}}}},
    {"name": "get_item", "description": "One item in full.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "mark_done", "description": "Clear items from the inbox.",
     "inputSchema": {"type": "object", "properties": {"ids": IDS}, "required": ["ids"]}},
    {"name": "snooze", "description": "Hide items until a time: '1h', '30m', '2d', 'tonight', 'tomorrow' (9am) or ISO 8601.",
     "inputSchema": {"type": "object", "properties": {"ids": IDS, "until": {"type": "string"}},
                     "required": ["ids", "until"]}},
    {"name": "open_item", "description": "Open the item's deep link on the user's computer (the app, thread or page).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "fix_link", "description": "Teach pile where an item should really open. Pass the URL the user ended up on; "
     "pile generalises it to the whole app when the URL contains the item's sender/title, else keeps it to that sender.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "url": {"type": "string"}},
                     "required": ["id", "url"]}},
    {"name": "set_rule", "description": "Teach pile a rule. kind=label with value needs-you|fyi|noise; kind=mute "
     "(skip an app entirely); kind=link with a template like https://example.com/{sender:slug}. match.app is a "
     "bundle id/app key from list_inbox (omit for any app); match.sender narrows to one sender/thread.",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(rules.KINDS)},
         "match": {"type": "object", "properties": {"app": {"type": "string"}, "sender": {"type": "string"}}},
         "value": {"type": "string"}}, "required": ["kind"]}},
    {"name": "list_rules", "description": "Everything pile has learned (link fixes, labels, muted apps).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "delete_rule", "description": "Forget a learned rule.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
]


def _brief(item: dict) -> dict:
    out = {k: item[k] for k in ("id", "source", "app", "app_name", "sender", "title", "body", "state", "label", "link")}
    out["when"] = datetime.fromtimestamp(item["at"]).isoformat(timespec="minutes")
    if item["link_learned"]:
        out["link_learned"] = True
    if item.get("snooze_until"):
        out["snoozed_until"] = datetime.fromtimestamp(item["snooze_until"]).isoformat(timespec="minutes")
    return out


def call(store, name: str, a: dict):
    if name == "list_inbox":
        items = store.items(a.get("state", "new"), a.get("source"), a.get("label"), int(a.get("limit", 50)))
        return {"count": len(items), "stats": store.stats(), "items": [_brief(i) for i in items]}
    if name == "get_item":
        item = store.get(a["id"])
        if not item:
            raise KeyError("no item %s" % a["id"])
        return _brief(item)
    if name == "mark_done":
        return actions.done(store, a["ids"])
    if name == "snooze":
        return actions.snooze(store, a["ids"], a["until"])
    if name == "open_item":
        return actions.open_item(store, a["id"])
    if name == "fix_link":
        return actions.fix_link(store, a["id"], a["url"])
    if name == "set_rule":
        m = a.get("match") or {}
        return rules.add(store, a["kind"], a.get("value", ""), app=m.get("app"), sender=m.get("sender"),
                         note="set by agent")
    if name == "list_rules":
        return {"rules": rules.all_rules(store)}
    if name == "delete_rule":
        return {"deleted": rules.delete(store, a["id"])}
    raise KeyError("unknown tool " + name)


def handle(store, msg: dict):
    """One JSON-RPC message in, one response out (None for notifications)."""
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None
    try:
        if method == "initialize":
            ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
            result = {"protocolVersion": ver, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "pile", "version": __version__},
                      "instructions": "pile is the user's unified inbox: every notification and unread item. "
                      "Use list_inbox to see it, then mark_done/snooze/open_item. When the user corrects you "
                      "(wrong link, wrong importance), teach pile with fix_link or set_rule."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                out = call(store, p.get("name"), p.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]}
            except (KeyError, ValueError, TypeError) as e:
                result = {"content": [{"type": "text", "text": "error: %s" % e}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def serve(store, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(store, msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
