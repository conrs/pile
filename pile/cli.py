"""pile: one local inbox for every notification and unread."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, collectors, demo, rules, store, triage


def open_store(args):
    path = args.db
    if args.demo and not path:
        path = os.path.join(os.path.dirname(store.default_path()), "demo.db")
    s = store.Store(path)
    if not args.demo:
        from . import writeback
        writeback.attach(s)
    if args.demo and args.cmd == "serve":  # every demo starts fresh, with times relative to now
        s.db.execute("DELETE FROM items")
        s.db.execute("DELETE FROM rules")
    if args.demo and not s.db.execute("SELECT 1 FROM items LIMIT 1").fetchone():
        demo.seed(s)
    return s


def doctor(s, kw):
    import platform
    print("pile %s on %s (Python %s)" % (__version__, collectors.this_os(), platform.python_version()))
    home = os.path.expanduser("~")
    print("database: %s\n" % (("~" + s.path[len(home):]) if s.path.startswith(home) else s.path))
    for mod, ok, msg in collectors.status(kw):
        mark = {True: "ok ", False: "-- ", None: "   "}[ok]
        print("  %s %-9s %s" % (mark, mod.NAME, msg))
        if ok and hasattr(mod, "doctor_lines"):
            for good, line in mod.doctor_lines():
                print("      %s  %s" % ("ok" if good else "!!", line))
    from . import writeback
    print("\n  writeback " + ("on: done marks mail read and GitHub threads done" if writeback.enabled()
                               else "off (PILE_WRITEBACK=1: done also marks mail read and GitHub threads done)"))
    print("  triage    " + ("model: %s" % triage.config()[1] if triage.config()
                               else "off (set PILE_BASE_URL and PILE_MODEL to label with a model)"))
    from . import redact
    cfg = triage.config()
    cloud = bool(cfg) and not any(h in cfg[0] for h in ("localhost", "127.0.0.1", "[::1]"))
    print("  redaction " + ("on for MCP (PILE_REDACT)" if redact.enabled() else "off: agents see real names")
          + ("; your triage model is in the cloud, so consider PILE_REDACT=1 / `pile mcp --redact`" if cloud and not redact.enabled() else ""))
    from . import envfile
    print("  settings from: %s" % (", ".join(("~" + f[len(home):]) if f.startswith(home) else f for f in envfile.LOADED)
                                   or "environment only (none of ~/.config/pile/env, ./.env)"))
    ud = collectors.user_dir()
    print("  your collectors: %s" % (("~" + ud[len(home):]) if ud.startswith(home) else ud))


def main(argv=None):
    from . import envfile
    envfile.load()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help="SQLite file (default ~/.local/share/pile/inbox.db)")
    common.add_argument("--demo", action="store_true", default=argparse.SUPPRESS,
                        help="use a separate demo database seeded with made-up items")
    common.add_argument("--macos-db", default=argparse.SUPPRESS,
                        help="read this macOS Notification Center database (a copy or a fixture)")
    common.add_argument("--windows-db", default=argparse.SUPPRESS,
                        help="read this Windows wpndatabase.db (a copy or a fixture)")
    common.add_argument("--messages-db", default=argparse.SUPPRESS,
                        help="read this macOS Messages chat.db (a copy or a fixture)")
    p = argparse.ArgumentParser(prog="pile", description=__doc__, parents=[common])
    p.add_argument("--version", action="version", version="pile " + __version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser(parents=[common], name="doctor", help="which collectors work on this machine, and how to turn on the rest")
    sub.add_parser(parents=[common], name="collect", help="run every ready collector once")
    w = sub.add_parser(parents=[common], name="watch", help="collect every N seconds")
    w.add_argument("--every", type=int, default=30)
    sv = sub.add_parser(parents=[common], name="serve", help="the web UI on http://127.0.0.1:8765")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--watch", type=int, default=30, help="collect every N seconds while serving (0 = off)")
    sv.add_argument("--listen", default="127.0.0.1",
                    help="address to bind; 0.0.0.0 lets phones on your LAN POST /ingest (the UI stays local-only)")
    mc = sub.add_parser(parents=[common], name="mcp", help="MCP server on stdio, for Claude and other agents")
    mc.add_argument("--redact", action="store_true",
                    help="agents see pseudonyms instead of names and contacts, and no secrets (also PILE_REDACT=1)")
    ex = sub.add_parser(parents=[common], name="export", help="dump items, e.g. redacted for a bug report or demo")
    ex.add_argument("--redacted", action="store_true", help="pseudonyms for people, no secrets or contacts")
    ex.add_argument("--json", action="store_true")
    ex.add_argument("--state", default="all", choices=["new", "done", "snoozed", "all"])
    sub.add_parser(parents=[common], name="triage", help="label new items: your rules first, then a model if configured")
    sub.add_parser(parents=[common], name="rules", help="print what pile has learned")
    ls = sub.add_parser(parents=[common], name="list", help="print the inbox")
    ls.add_argument("--state", default="new", choices=["new", "done", "snoozed", "all"])
    ls.add_argument("--source")
    ls.add_argument("--json", action="store_true")
    bf = sub.add_parser(parents=[common], name="backfill",
                        help="pull older things you never followed up on (Messages, GitHub, mail)")
    bf.add_argument("--days", type=float, default=30)
    hi = sub.add_parser(parents=[common], name="history", help="what you did, newest first")
    hi.add_argument("--limit", type=int, default=50)
    hi.add_argument("--json", action="store_true")
    ot = sub.add_parser(parents=[common], name="open-test", help="show which link `open` would use for an item, and why")
    ot.add_argument("id", type=int)
    un = sub.add_parser(parents=[common], name="undo", help="undo the last change (repeat to go further back)")
    un.add_argument("--steps", type=int, default=1)
    rd = sub.add_parser(parents=[common], name="redo", help="redo what undo undid")
    rd.add_argument("--steps", type=int, default=1)
    ro = sub.add_parser(parents=[common], name="reopen", help="put done and snoozed items back in the inbox")
    ro.add_argument("--since", help="only items cleared in the last 30m, 2h, 1d ...")
    ro.add_argument("--all", action="store_true", help="every done and snoozed item")
    ro.add_argument("--source")
    sub.add_parser(parents=[common], name="token", help="print the token for POST /ingest")
    args = p.parse_args(argv)
    for k in ("db", "demo", "macos_db", "windows_db", "messages_db"):
        if not hasattr(args, k):
            setattr(args, k, False if k == "demo" else None)
    if not args.cmd:
        p.print_help()
        return
    s = open_store(args)
    kw = {k: v for k, v in (("macos_db", args.macos_db), ("windows_db", args.windows_db),
                            ("messages_db", args.messages_db)) if v}

    if args.cmd == "doctor":
        doctor(s, kw)
    elif args.cmd == "token":
        from .collectors import webhook
        print(webhook.token(s))
    elif args.cmd == "collect" and args.demo:
        print(json.dumps({"demo": len(s.items())}))
    elif args.cmd == "collect":
        print(json.dumps(collectors.run_all(s, **kw)))
    elif args.cmd == "watch":
        import threading
        collectors.start_listeners(s, threading.Lock(), threading.Event(), **kw)
        while True:
            counts = collectors.run_all(s, **kw)
            print(time.strftime("%H:%M:%S"), json.dumps(counts), flush=True)
            time.sleep(args.every)
    elif args.cmd == "serve":
        from . import server
        server.serve(s, args.port, 0 if args.demo else args.watch, dict(kw, demo=True) if args.demo else kw, args.listen)
    elif args.cmd == "mcp":
        from . import mcp
        from . import redact
        mcp.serve(s, redact=args.redact or redact.enabled())
    elif args.cmd == "triage":
        r = triage.run(s)
        if not r["model"]:
            print("pile: no model configured (set PILE_BASE_URL, PILE_MODEL, PILE_API_KEY); applied your rules only",
                  file=sys.stderr)
        print(json.dumps(r))
    elif args.cmd == "rules":
        for r in rules.all_rules(s):
            print("%-3d %-5s %-28s %-24s %s" % (r["id"], r["kind"], r["app"] or "*", (r["sender"] or "*")[:24], r["value"]))
    elif args.cmd == "backfill":
        print(json.dumps(collectors.run_backfill(s, args.days, **kw)))
        print("pile: %d unanswered in the inbox; see the Unanswered tab" % s.stats()["unanswered"], file=sys.stderr)
    elif args.cmd == "history":
        rows = s.history(args.limit)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        for r in [] if args.json else rows:
            what = " ".join("%s=%s" % kv for kv in r["detail"].items())
            print("%s  %-8s %5s  %-10s %-22s %s" % (time.strftime("%m-%d %H:%M", time.localtime(r["at"])), r["action"],
                                                  r["item_id"] or "", (r["app_name"] or "")[:10],
                                                  (r["sender"] or r["title"] or "")[:22], what[:60]))
    elif args.cmd == "export":
        items = s.items(args.state, limit=100000)
        if args.redacted:
            from .redact import Redactor
            r = Redactor(s)
            items = [r.item(i) for i in items]
        keep = ("id", "source", "app", "app_name", "thread", "sender", "title", "body", "at", "state", "label", "tag", "link")
        items = [{k: i.get(k) for k in keep} for i in items]
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=1))
        else:
            for i in items:
                print("%s  %-10s %-14s %s" % (time.strftime("%m-%d %H:%M", time.localtime(i["at"])), i["app_name"][:10],
                                              (i["sender"] or "")[:14], (i["title"] + " " + i["body"]).strip()[:90]))
    elif args.cmd == "open-test":
        from .actions import open_test
        r = open_test(s, args.id)
        print("item %(id)s: %(app)s · %(sender)s %(title)s" % r)
        print("would open: %s%s" % (r["link"], " (learned)" if r["learned"] else ""))
        if r["was"] != r["link"]:
            print("was:        " + r["was"])
        for w in r["why"]:
            print("why:        " + w)
    elif args.cmd in ("undo", "redo"):
        r = s.undo(args.steps) if args.cmd == "undo" else s.redo(args.steps)
        for what in r["undid" if args.cmd == "undo" else "redid"] or ["nothing to %s" % args.cmd]:
            print(("undid: " if args.cmd == "undo" else "redid: ") + what if not what.startswith("nothing") else what)
        print("%d more to undo, %d to redo" % (r["remaining"], r["redoable"]), file=sys.stderr)
    elif args.cmd == "reopen":
        if not (args.since or args.all or args.source):
            p.error("reopen needs --since, --all or --source")
        from .actions import parse_until
        since = None
        if args.since:
            since = time.time() - (parse_until(args.since) - time.time())
        with s.recording("reopen_all", (), since=args.since, source=args.source):
            n = s.reopen(since, args.source)
        print("reopened %d" % n)
    elif args.cmd == "list":
        items = s.items(args.state, args.source)
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=1))
            return
        for i in items:
            print("%5d  %-9s %-10s %-22s %s" % (i["id"], (i["tag"] or i["label"] or "")[:9], i["app_name"][:10],
                                              (i["sender"] or "")[:22], (i["title"] + " " + i["body"]).strip()[:70]))
