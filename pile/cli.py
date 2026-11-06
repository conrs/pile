"""pile collect | watch | serve | mcp | triage | rules | demo"""
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
    if args.demo and not s.db.execute("SELECT 1 FROM items LIMIT 1").fetchone():
        demo.seed(s)
    return s


def main(argv=None):
    p = argparse.ArgumentParser(prog="pile", description=__doc__)
    p.add_argument("--db", help="SQLite file (default ~/.local/share/pile/inbox.db)")
    p.add_argument("--demo", action="store_true", help="use a separate demo database seeded with fake items")
    p.add_argument("--macos-db", help="read this Notification Center database instead of the system one")
    p.add_argument("--version", action="version", version="pile " + __version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("collect", help="run every configured collector once")
    w = sub.add_parser("watch", help="collect every N seconds")
    w.add_argument("--every", type=int, default=30)
    sv = sub.add_parser("serve", help="the web UI on http://127.0.0.1:8765")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--watch", type=int, default=30, help="collect every N seconds while serving (0 = off)")
    sub.add_parser("mcp", help="MCP server on stdio, for Claude and other agents")
    sub.add_parser("triage", help="label new items: your rules first, then a model if configured")
    sub.add_parser("rules", help="print what pile has learned")
    sub.add_parser("list", help="print the inbox")
    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return
    s = open_store(args)
    kw = {"macos_db": args.macos_db}

    if args.cmd == "collect":
        print(json.dumps(collectors.run_all(s, **kw)))
    elif args.cmd == "watch":
        while True:
            counts = collectors.run_all(s, **kw)
            print(time.strftime("%H:%M:%S"), json.dumps(counts), flush=True)
            time.sleep(args.every)
    elif args.cmd == "serve":
        from . import server
        server.serve(s, args.port, 0 if args.demo else args.watch, kw)
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
