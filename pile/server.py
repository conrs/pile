"""The local web UI: one page plus a small JSON API, bound to 127.0.0.1."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import actions, collectors, rules, triage
from .collectors import webhook

UI = os.path.join(os.path.dirname(__file__), "ui.html")


class Presence:
    """The driving tab's live view, for follower tabs (screen sharing). Memory only."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0          # bumps on every publish or data change
        self.version = 0      # bumps on every data change, so followers refetch items
        self.state = None

    def publish(self, state):
        with self.cond:
            self.state, self.seq = state, self.seq + 1
            self.cond.notify_all()

    def changed(self):
        with self.cond:
            self.version += 1
            self.seq += 1
            self.cond.notify_all()

    def wait(self, since, timeout=15.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq > since, timeout=timeout)
            return {"seq": self.seq, "version": self.version, "state": self.state}


def make_handler(store, lock, collect_kwargs, presence=None):
    presence = presence or Presence()

    def post_routes():
        return {
            "/api/done": lambda b: actions.done(store, b["ids"]),
            "/api/reopen": lambda b: actions.reopen(store, b["ids"]),
            "/api/snooze": lambda b: actions.snooze(store, b["ids"], b["until"]),
            "/api/open": lambda b: actions.open_item(store, b["id"], bool(b.get("browser_opened"))),
            "/api/fix_link": lambda b: actions.fix_link(store, b["id"], b["url"]),
            "/api/label": lambda b: actions.teach_labels(store, b.get("ids") or [b["id"]], b["label"], b.get("scope", "sender")),
            "/api/mute": lambda b: actions.mute_apps(store, b.get("ids") or [b["id"]]),
            "/api/combine": lambda b: actions.combine(store, b["ids"]),
            "/api/undo": lambda b: actions.undo(store, b.get("steps", 1)),
            "/api/redo": lambda b: actions.redo(store, b.get("steps", 1)),
            "/api/group": lambda b: actions.group(store, b["id"], b["with"]),
            "/api/ungroup": lambda b: actions.ungroup(store, b["id"]),
            "/api/rules/delete": lambda b: {"deleted": rules.delete(store, b["id"])},
            "/api/collect": lambda b: ({"demo": 0} if collect_kwargs.get("demo")
                                       else collectors.run_all(store, quiet=True, **collect_kwargs)),
            "/api/triage": lambda b: triage.run(store),
        }

    class Handler(BaseHTTPRequestHandler):
        server_version = "pile"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _local(self):
            # Refuse DNS-rebinding: only answer to our own host names.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost", "[::1]")

        def do_GET(self):
            if not self._local():
                return self._send(403, {"error": "forbidden host"})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/presence":  # long poll: answers as soon as the driver changes anything
                return self._send(200, presence.wait(int(q.get("since", 0) or 0), min(float(q.get("wait", 15)), 25)))
            with lock:
                if u.path == "/":
                    with open(UI, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if u.path == "/api/items":
                    from . import redact
                    items = store.items(q.get("state", "new"), q.get("source") or None)
                    if q.get("names"):  # screen-share with pseudonyms instead of blur
                        r = redact.Redactor(store)
                        for it in items:
                            red = r.item(it)
                            it["r"] = {k: red[k] for k in ("sender", "title", "body")}
                    return self._send(200, {"items": items, "stats": store.stats(), "triage": bool(triage.config()),
                                            "redact": redact.enabled()})
                if u.path == "/api/rules":
                    return self._send(200, {"rules": rules.all_rules(store)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path == "/ingest":  # token-gated, so it may face the LAN
                if not webhook.authorized(store, self.headers.get("Authorization")):
                    return self._send(401, {"error": "bad or missing token (see `pile token`)"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 65536:
                        return self._send(413, {"error": "too big"})
                    with lock:
                        out = webhook.ingest(store, json.loads(self.rfile.read(n) or b"{}"))
                    presence.changed()
                    return self._send(200, out)
                except (ValueError, TypeError) as e:
                    return self._send(400, {"error": str(e)})
            # JSON content type forces a CORS preflight, which we never answer: no cross-site POSTs.
            if not self._local() or not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(403, {"error": "forbidden"})
            path = urlparse(self.path).path
            if path == "/api/presence":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 262144:
                    return self._send(413, {"error": "too big"})
                presence.publish(json.loads(self.rfile.read(n) or b"{}"))
                return self._send(200, {"ok": True})
            fn = post_routes().get(path)
            if not fn:
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with lock:
                    out = fn(body)
                if path not in ("/api/open",):
                    presence.changed()
                return self._send(200, out)
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": str(e)})

    return Handler


def serve(store, port=8765, watch=0, collect_kwargs=None, host="127.0.0.1"):
    lock = threading.Lock()
    presence = Presence()
    collect_kwargs = collect_kwargs or {}
    webhook.token(store)
    if watch:
        collectors.start_listeners(store, lock, threading.Event(), **collect_kwargs)
    if watch:
        def loop():
            ev = threading.Event()
            while True:
                with lock:
                    counts = collectors.run_all(store, quiet=True, **collect_kwargs)
                if any(isinstance(v, int) and v for v in counts.values()):
                    presence.changed()
                ev.wait(watch)
        threading.Thread(target=loop, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(store, lock, collect_kwargs, presence))
    print("pile: http://127.0.0.1:%d  (Ctrl-C to stop)" % port)
    if host != "127.0.0.1":
        print("pile: POST /ingest also open on %s:%d (token-protected; the UI stays local-only)" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
