import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pile import server, store
from pile.collectors import webhook


class Far:
    """A loopback service the buttons call; records what it got and answers with `reply`."""

    def __init__(self, reply=(200, {"status": "declined"}), location=None):
        self.got = []
        far = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                far.got.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(n).decode()})
                code, body = reply
                data = json.dumps(body).encode()
                self.send_response(code)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ActionsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._home, os.environ["HOME"] = os.environ.get("HOME"), self.home
        os.makedirs(os.path.join(self.home, ".config", "svc"))
        self.secret = os.path.join(self.home, ".config", "svc", "local.token")
        with open(self.secret, "w") as f:
            f.write("s3cret-value\n")
        self.s = store.Store(":memory:")
        self.tok = webhook.token(self.s)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.s, threading.Lock(), {}))
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.far = Far()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.far.close()
        os.environ["HOME"] = self._home

    def call(self, path, body, token=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def ingest(self, acts, **kw):
        return self.call("/ingest", dict({"app": "Scheduler", "title": "Sam wants Thu 8:00", "id": "sched-p1-v1",
                                          "actions": acts}, **kw), self.tok)

    def button(self, label="No", path="/local/proposals/p1/decide", **kw):
        return dict({"label": label, "url": self.far.url + path, "method": "POST",
                     "headersFromFile": {"x-local-token": "~/.config/svc/local.token"}, "body": {"decision": "no"}}, **kw)

    def items(self, state="new"):
        with urllib.request.urlopen(self.base + "/api/items?state=" + state) as r:
            raw = r.read().decode()
        return raw, json.loads(raw)["items"]

    def test_buttons_run_server_side_with_the_secret_and_mark_done_undoably(self):
        code, out = self.ingest([self.button("Yes: Thu 11:00", body={"decision": "yes", "pick": 2}), self.button()])
        self.assertEqual(code, 200)
        raw, items = self.items()
        self.assertEqual(items[0]["actions"], [{"label": "Yes: Thu 11:00"}, {"label": "No"}])
        # The page never sees the secret, the header name or the far end's URL.
        for leak in ("s3cret", "x-local-token", "local.token", self.far.url):
            self.assertNotIn(leak, raw)
        code, r = self.call("/api/action", {"id": out["id"], "index": 1})
        self.assertEqual((code, r["done"], r["label"], r["result"]), (200, 1, "No", "declined"))
        got = self.far.got[0]
        self.assertEqual(got["path"], "/local/proposals/p1/decide")
        self.assertEqual(got["headers"]["x-local-token"], "s3cret-value")
        self.assertEqual(json.loads(got["body"]), {"decision": "no"})
        self.assertEqual(self.s.get(out["id"])["state"], "done")
        self.assertEqual(self.call("/api/undo", {})[1]["undid"][0].split(" · ")[0], "action")
        self.assertEqual(self.s.get(out["id"])["state"], "new")

    def test_far_end_refusal_leaves_the_item_and_says_why(self):
        self.far.close()
        self.far = Far(reply=(409, {"error": "not_pending", "status": "declined"}))
        _, out = self.ingest([self.button()])
        code, r = self.call("/api/action", {"id": out["id"], "index": 0})
        self.assertEqual(code, 502)
        self.assertIn("409", r["error"])
        self.assertEqual(self.s.get(out["id"])["state"], "new")
        # Control: an unknown button is a 400, and nothing is sent.
        n = len(self.far.got)
        self.assertEqual(self.call("/api/action", {"id": out["id"], "index": 5})[0], 400)
        self.assertEqual(len(self.far.got), n)

    def test_only_loopback_urls_and_secrets_under_config(self):
        for bad in ("http://example.com/x", "https://127.0.0.1/x", "http://user:pw@127.0.0.1/x",
                    "http://127.0.0.1.example.com/x", "file:///etc/passwd"):
            code, r = self.ingest([self.button(url=bad)])
            self.assertEqual(code, 400, bad)
            self.assertIn("url", r["error"])
        code, r = self.ingest([self.button(headersFromFile={"x": "/etc/passwd"})])
        self.assertEqual((code, "headersFromFile" in r["error"]), (400, True))
        code, r = self.ingest([self.button(headersFromFile={"x": "~/.config/../.ssh/id_rsa"})])
        self.assertEqual(code, 400)
        # Control: localhost is fine.
        self.assertEqual(self.ingest([self.button(url="http://localhost:9/x")])[0], 200)

    def test_a_redirect_is_not_followed(self):
        other = Far()
        self.far.close()
        self.far = Far(reply=(302, {}), location=other.url + "/stolen")
        try:
            _, out = self.ingest([self.button()])
            code, _ = self.call("/api/action", {"id": out["id"], "index": 0})
            self.assertEqual(code, 502)
            self.assertEqual(other.got, [])
            self.assertEqual(len(self.far.got), 1)  # control: the first hop was made
        finally:
            other.close()

    def test_a_new_version_replaces_buttons_and_forwarded_items_never_carry_them(self):
        _, out = self.ingest([self.button()])
        self.ingest([], at=9999999999)
        self.assertEqual(self.s.get(out["id"])["actions"], [])
        code, f = self.call("/ingest", {"origin": "mac", "source": "webhook", "id": "x1", "app": "hook:boardy",
                                        "title": "t", "actions": [self.button()]}, self.tok)
        self.assertEqual(code, 200)
        self.assertEqual(self.s.buttons(f["id"]), [])

    def test_the_sender_may_label_an_item_unless_a_rule_says_otherwise(self):
        _, a = self.ingest([], label="needs-you")
        self.assertEqual((self.s.get(a["id"])["label"], self.s.get(a["id"])["label_by"]), ("needs-you", "sender"))
        self.assertEqual(self.ingest([], id="x2", label="urgent")[0], 400)
        # Control: your own rule wins over the sender's label.
        self.call("/api/label", {"id": a["id"], "label": "fyi"})
        _, b = self.ingest([], id="x3", label="needs-you")
        self.assertEqual(self.s.get(b["id"])["label"], "fyi")


if __name__ == "__main__":
    unittest.main()
