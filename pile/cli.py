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
    print("\n  triage    " + ("model: %s" % triage.config()[1] if triage.config()
                               else "off (set PILE_BASE_URL and PILE_MODEL to label with a model)"))
    ud = collectors.user_dir()
    print("  your collectors: %s" % (("~" + ud[len(home):]) if ud.startswith(home) else ud))


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help="SQLite file (default ~/.local/share/pile/inbox.db)")
    common.add_argument("--demo", action="store_true", default=argparse.SUPPRESS,
                        help="use a separate demo database seeded with made-up items")
    common.add_argument("--macos-db", default=argparse.SUPPRESS,
                        help="read this macOS Notification Center database (a copy or a fixture)")
    common.add_argument("--windows-db", default=argparse.SUPPRESS,
                        help="read this Windows wpndatabase.db (a copy or a fixture)")
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
    sub.add_parser(parents=[common], name="mcp", help="MCP server on stdio, for Claude and other agents")
    sub.add_parser(parents=[common], name="triage", help="label new items: your rules first, then a model if configured")
    sub.add_parser(parents=[common], name="rules", help="print what pile has learned")
    sub.add_parser(parents=[common], name="list", help="print the inbox")
    sub.add_parser(parents=[common], name="token", help="print the token for POST /ingest")
    args = p.parse_args(argv)
    for k in ("db", "demo", "macos_db", "windows_db"):
        if not hasattr(args, k):
            setattr(args, k, False if k == "demo" else None)
    if not args.cmd:
        p.print_help()
        return
    s = open_store(args)
    kw = {k: v for k, v in (("macos_db", args.macos_db), ("windows_db", args.windows_db)) if v}

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
        mcp.serve(s)
    elif args.cmd == "triage":
        r = triage.run(s)
        if not r["model"]:
            print("pile: no model configured (set PILE_BASE_URL, PILE_MODEL, PILE_API_KEY); applied your rules only",
                  file=sys.stderr)
        print(json.dumps(r))
    elif args.cmd == "rules":
        for r in rules.all_rules(s):
            print("%-3d %-5s %-28s %-24s %s" % (r["id"], r["kind"], r["app"] or "*", (r["sender"] or "*")[:24], r["value"]))
    elif args.cmd == "list":
        for i in s.items():
            print("%5d  %-9s %-10s %-22s %s" % (i["id"], (i["label"] or "")[:9], i["app_name"][:10],
                                              (i["sender"] or "")[:22], (i["title"] + " " + i["body"]).strip()[:70]))
