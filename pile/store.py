"""One SQLite file holds every item, the collectors' watermarks and the learned rules."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,          -- macos | github | imap | demo ...
  ext_id TEXT NOT NULL,          -- the source's own id, for dedupe
  app TEXT NOT NULL,             -- bundle id, or a source-specific app key
  app_name TEXT NOT NULL,
  thread TEXT NOT NULL,          -- grouping key (person, channel, PR ...)
  sender TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  at REAL NOT NULL,              -- unix seconds
  url TEXT NOT NULL DEFAULT '',  -- built-in deep link; learned rules override at read time
  state TEXT NOT NULL DEFAULT 'new',  -- new | done | snoozed
  snooze_until REAL,
  label TEXT,                    -- needs-you | fyi | noise
  label_by TEXT,                 -- rule | model | demo
  UNIQUE (source, ext_id)
);
CREATE INDEX IF NOT EXISTS items_state_at ON items (state, at DESC);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  at REAL NOT NULL,
  action TEXT NOT NULL,          -- open | done | reopen | snooze | label | mute | fix_link | group | ...
  item_id INTEGER,
  detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,            -- link | label | mute
  app TEXT,                      -- NULL = any app
  sender TEXT,                   -- NULL = anyone in that app
  value TEXT NOT NULL DEFAULT '',-- link template | label | ''
  note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
"""

FIELDS = ("id", "source", "ext_id", "app", "app_name", "thread", "sender", "title",
          "body", "at", "url", "state", "snooze_until", "label", "label_by", "tag", "changed_at")


def default_path() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "pile", "inbox.db")


class Store:
    def __init__(self, path: Optional[str] = None):
        self.path = path or default_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(items)")}
        for col, decl in (("tag", "TEXT"), ("changed_at", "REAL")):  # added after 0.1.0
            if col not in cols:
                self.db.execute("ALTER TABLE items ADD COLUMN %s %s" % (col, decl))
        # 0.1.x grouped group chats by sender; re-key them by the group's name (kept in title).
        from .collectors.macos import GROUP_CHAT_APPS
        marks = ",".join("?" * len(GROUP_CHAT_APPS))
        self.db.execute("UPDATE items SET thread = app || '|t:g:' || title WHERE source='macos' AND title != ''"
                        " AND thread NOT LIKE '%|t:%' AND app IN (" + marks + ")", GROUP_CHAT_APPS)

    # ---- meta (watermarks) ----
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    # ---- items ----
    def upsert(self, item: dict) -> Optional[int]:
        """Insert a new item, or refresh an existing one. A newer `at` reopens it."""
        from . import rules  # late import: rules imports store types only
        if rules.is_muted(self, item["app"]):
            return None
        item = dict(item)
        item.setdefault("thread", item.get("sender") or item.get("title") or item["ext_id"])
        for k in ("sender", "title", "body", "url"):
            item[k] = (item.get(k) or "")
        old = self.db.execute("SELECT id, at FROM items WHERE source=? AND ext_id=?",
                              (item["source"], item["ext_id"])).fetchone()
        if old:
            reopen = item["at"] > old["at"] + 0.5
            self.db.execute(
                "UPDATE items SET app=?, app_name=?, thread=?, sender=?, title=?, body=?, at=?, url=?"
                + (", state='new', snooze_until=NULL" if reopen else "") + " WHERE id=?",
                (item["app"], item["app_name"], item["thread"], item["sender"], item["title"],
                 item["body"], item["at"], item["url"], old["id"]))
            if item.get("tag"):
                self.db.execute("UPDATE items SET tag=? WHERE id=?", (item["tag"], old["id"]))
            return old["id"]
        label, label_by = item.get("label"), item.get("label_by")
        if not label:
            label = rules.label_for(self, item)
            label_by = "rule" if label else None
        cur = self.db.execute(
            "INSERT INTO items(source, ext_id, app, app_name, thread, sender, title, body, at, url, state, label, label_by, tag)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item["source"], item["ext_id"], item["app"], item["app_name"], item["thread"], item["sender"],
             item["title"], item["body"], item["at"], item["url"], item.get("state", "new"), label, label_by,
             item.get("tag")))
        return cur.lastrowid

    def _wake_snoozed(self) -> None:
        self.db.execute("UPDATE items SET state='new', snooze_until=NULL WHERE state='snoozed' AND snooze_until<=?",
                        (time.time(),))

    def items(self, state: str = "new", source: Optional[str] = None, label: Optional[str] = None,
              limit: int = 500) -> list:
        self._wake_snoozed()
        q, args = "SELECT * FROM items WHERE 1=1", []
        if state and state != "all":
            q += " AND state=?"; args.append(state)
        if source:
            q += " AND source=?"; args.append(source)
        if label:
            q += " AND label=?"; args.append(label)
        q += " ORDER BY at DESC LIMIT ?"; args.append(int(limit))
        return [self.present(dict(r)) for r in self.db.execute(q, args)]

    def get(self, item_id: int) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM items WHERE id=?", (int(item_id),)).fetchone()
        return self.present(dict(r)) if r else None

    def present(self, item: dict) -> dict:
        from . import rules
        item["raw_thread"] = item["thread"]
        item["thread"] = rules.thread_for(self, item)
        link, learned = rules.resolve_link(self, item)
        item["link"], item["link_learned"] = link, learned
        return item

    def set_state(self, ids: Iterable[int], state: str, snooze_until: Optional[float] = None) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = self.db.execute(f"UPDATE items SET state=?, snooze_until=?, changed_at=? WHERE id IN ({marks})",
                              [state, snooze_until, time.time()] + ids)
        return cur.rowcount

    def set_label(self, item_id: int, label: Optional[str], by: str) -> None:
        self.db.execute("UPDATE items SET label=?, label_by=? WHERE id=?", (label, by, int(item_id)))

    def close_missing(self, source: str, live_ext_ids: Iterable[str]) -> int:
        """Mark done the open items a source no longer reports (read elsewhere)."""
        live = set(live_ext_ids)
        gone = [r["id"] for r in self.db.execute(
            "SELECT id, ext_id FROM items WHERE source=? AND state!='done' AND tag IS NULL", (source,))
            if r["ext_id"] not in live]
        return self.set_state(gone, "done")

    def log(self, action: str, item_ids=(None,), **detail) -> None:
        import json
        now = time.time()
        for i in (item_ids or (None,)):
            self.db.execute("INSERT INTO actions(at, action, item_id, detail) VALUES(?,?,?,?)",
                            (now, action, i, json.dumps(detail, ensure_ascii=False)))

    def history(self, limit: int = 200) -> list:
        import json
        out = []
        for r in self.db.execute("SELECT a.*, i.app_name, i.sender, i.title FROM actions a LEFT JOIN items i"
                                 " ON i.id = a.item_id ORDER BY a.at DESC, a.id DESC LIMIT ?", (int(limit),)):
            r = dict(r)
            r["detail"] = json.loads(r["detail"] or "{}")
            out.append(r)
        return out

    def reopen(self, since: Optional[float] = None, source: Optional[str] = None) -> int:
        """Put done/snoozed items back in the inbox. Rules are kept."""
        q, args = "UPDATE items SET state='new', snooze_until=NULL, changed_at=? WHERE state!='new'", [time.time()]
        if since is not None:
            q += " AND changed_at >= ?"; args.append(since)
        if source:
            q += " AND source=?"; args.append(source)
        return self.db.execute(q, args).rowcount

    def stats(self) -> dict:
        self._wake_snoozed()
        out = {"new": 0, "snoozed": 0, "done": 0, "sources": {}, "labels": {}}
        for r in self.db.execute("SELECT state, COUNT(*) n FROM items GROUP BY state"):
            out[r["state"]] = r["n"]
        for r in self.db.execute("SELECT source, COUNT(*) n FROM items WHERE state='new' GROUP BY source"):
            out["sources"][r["source"]] = r["n"]
        for r in self.db.execute("SELECT COALESCE(label,'') l, COUNT(*) n FROM items WHERE state='new' GROUP BY l"):
            out["labels"][r["l"] or "unlabelled"] = r["n"]
        out["unanswered"] = self.db.execute(
            "SELECT COUNT(*) n FROM items WHERE state='new' AND tag='unanswered'").fetchone()["n"]
        nxt = self.db.execute("SELECT MIN(snooze_until) t FROM items WHERE state='snoozed'").fetchone()
        out["next_wake"] = nxt["t"] if nxt else None
        return out
