"""Done in pile clears it at the source too: mail marked read over IMAP, GitHub threads marked done.

Opt-in (PILE_WRITEBACK=1): without it pile only reads. Undo puts mail back to unread; GitHub has no
"unread" again, so an undone GitHub item stays in pile instead.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request

from .collectors import github, imap

ERRORS = []  # the latest failures, newest last, for `pile doctor` and the UI


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("PILE_WRITEBACK") or "").strip().lower() in ("1", "true", "yes", "on")


def transitions(change: dict):
    """(ids now done, ids reopened) from a recorded change: rows are [state, snooze, changed_at, label, label_by]."""
    before, after = change.get("items_before") or {}, change.get("items_after") or {}
    done = [int(i) for i in after if after[i][0] == "done" and before.get(i, [None])[0] != "done"]
    back = [int(i) for i in after if before.get(i, [None])[0] == "done" and after[i][0] != "done"]
    return done, back


def plan(store, change: dict) -> list:
    """What to tell each source, worked out on the caller's thread (SQLite stays on it)."""
    done, back = transitions(change)
    jobs = []
    for ids, read in ((done, True), (back, False)):
        for i in ids:
            r = store.db.execute("SELECT source, ext_id FROM items WHERE id=?", (i,)).fetchone()
            if not r:
                continue
            source, ext = r["source"], r["ext_id"]
            if source == "github" and ext.isdigit():
                if read:
                    jobs.append(("github", ext, True))
                else:  # can't be made unread at GitHub: keep it out of the next sync's "read elsewhere" sweep
                    store.db.execute("UPDATE items SET tag='earlier' WHERE id=?", (i,))
            elif (source == "imap" or source.startswith("imap:")) and ext.startswith("<"):
                jobs.append((source, ext, read))
    return jobs


def run(jobs: list) -> None:
    gh = [ext for kind, ext, read in jobs if kind == "github" and read]
    if gh:
        tok = github.token()
        for ext in gh:
            _try("github %s" % ext, lambda ext=ext: _github_done(tok, ext))
    by_source = {}
    for kind, ext, read in jobs:
        if kind != "github":
            by_source.setdefault(kind, []).append((ext, read))
    for source, msgs in by_source.items():
        acct = _account(source)
        if acct:
            _try(source, lambda acct=acct, msgs=msgs: _imap_flags(acct, msgs))


def _try(what, fn):
    try:
        fn()
    except Exception as e:
        ERRORS.append("%s: %s" % (what, e))
        del ERRORS[:-20]
        print("pile: couldn't update %s: %s" % (what, e), file=sys.stderr)


def _github_done(tok, thread_id):
    if not tok:
        raise RuntimeError("no GitHub token")
    req = urllib.request.Request(github.API + "/notifications/threads/" + thread_id, method="DELETE",
                                 headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
                                          "User-Agent": "pile"})
    urllib.request.urlopen(req, timeout=20).close()


def _account(source):
    accts = imap.accounts()
    if source == imap.NAME:
        return accts[0] if accts else None
    return next((a for a in accts if imap.source_for(a) == source), None)


def _imap_flags(acct, msgs):
    import imaplib
    conn = imaplib.IMAP4_SSL(acct["host"], timeout=30)
    try:
        conn.login(acct["user"], acct["password"])
        conn.select(acct["folder"])
        for mid, read in msgs:
            _, data = conn.search(None, "HEADER", "Message-ID", json.dumps(mid))
            for num in (data[0] or b"").split():
                conn.store(num, "+FLAGS" if read else "-FLAGS", "(\\Seen)")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def attach(store) -> None:
    """Hook a store so recorded changes (done, undo, redo) reach the sources, off the request thread."""
    if not enabled():
        return

    def on_change(change):
        jobs = plan(store, change)
        if jobs:
            threading.Thread(target=run, args=(jobs,), name="pile-writeback").start()

    store.on_change = on_change
