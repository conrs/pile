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


def make_handler(store, lock, collect_kwargs):
    def post_routes():
        return {
            "/api/done": lambda b: actions.done(store, b["ids"]),
            "/api/reopen": lambda b: actions.reopen(store, b["ids"]),
            "/api/snooze": lambda b: actions.snooze(store, b["ids"], b["until"]),
            "/api/open": lambda b: actions.open_item(store, b["id"]),
            "/api/fix_link": lambda b: actions.fix_link(store, b["id"], b["url"]),
            "/api/label": lambda b: actions.teach_label(store, b["id"], b["label"], b.get("scope", "sender")),
            "/api/mute": lambda b: actions.mute_app(store, b["id"]),
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
            with lock:
                if u.path == "/":
                    with open(UI, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if u.path == "/api/items":
                    return self._send(200, {"items": store.items(q.get("state", "new"), q.get("source") or None),
                                            "stats": store.stats(), "triage": bool(triage.config())})
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
                        return self._send(200, webhook.ingest(store, json.loads(self.rfile.read(n) or b"{}")))
                except (ValueError, TypeError) as e:
                    return self._send(400, {"error": str(e)})
            # JSON content type forces a CORS preflight, which we never answer: no cross-site POSTs.
            if not self._local() or not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(403, {"error": "forbidden"})
            fn = post_routes().get(urlparse(self.path).path)
            if not fn:
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with lock:
                    return self._send(200, fn(body))
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": str(e)})

    return Handler


def serve(store, port=8765, watch=0, collect_kwargs=None, host="127.0.0.1"):
    lock = threading.Lock()
    collect_kwargs = collect_kwargs or {}
    webhook.token(store)
    if watch:
        collectors.start_listeners(store, lock, threading.Event(), **collect_kwargs)
    if watch:
        def loop():
            ev = threading.Event()
            while True:
                with lock:
                    collectors.run_all(store, quiet=True, **collect_kwargs)
                ev.wait(watch)
        threading.Thread(target=loop, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(store, lock, collect_kwargs))
    print("pile: http://127.0.0.1:%d  (Ctrl-C to stop)" % port)
    if host != "127.0.0.1":
        print("pile: POST /ingest also open on %s:%d (token-protected; the UI stays local-only)" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
