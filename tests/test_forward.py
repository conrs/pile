import json
import os
import tempfile
import threading
import unittest
import urllib.request

from pile import actions, forward, server, store
from pile.collectors import webhook

T0 = 1793900000.0


def item(n, source="macos", at=None, **kw):
    d = {"source": source, "ext_id": "u%d" % n, "app": "com.tinyspeck.slackmacgap", "app_name": "Slack",
         "thread": "slack|t:g:#launch", "sender": "Priya", "title": "#launch", "body": "msg %d" % n,
         "at": at or T0 + n, "url": "slack://open"}
    d.update(kw)
    return d


class Hub:
    """A real `pile serve` handler on a free port, with its own store."""

    def __init__(self):
        self.s = store.Store(":memory:")
        self.tok = webhook.token(self.s)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.s, threading.Lock(), {}))
        self.url = "http://127.0.0.1:%d/ingest" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ForwardTest(unittest.TestCase):
    def setUp(self):
        self.mac = store.Store(os.path.join(tempfile.mkdtemp(), "mac.db"))
        self.hub = Hub()
        forward.setup(self.mac, 0)

    def tearDown(self):
        self.hub.close()

    def run_once(self, **kw):
        return forward.run_once(self.mac, self.hub.url, self.hub.tok, "mac", **kw)

    def test_sends_new_items_once_keeping_source_and_app(self):
        a = self.mac.upsert(item(1))
        self.mac.upsert(item(2, source="github", app="github", app_name="GitHub", url="https://github.com/x/y/pull/1"))
        self.assertEqual(self.run_once(), {"sent": 2, "refused": 0})
        got = {i["source"]: i for i in self.hub.s.items()}
        self.assertEqual(sorted(got), ["mac:github", "mac:macos"])
        slack = got["mac:macos"]
        self.assertEqual((slack["app"], slack["app_name"], slack["sender"], slack["body"], slack["raw_thread"]),
                         ("com.tinyspeck.slackmacgap", "Slack", "Priya", "msg 1", "slack|t:g:#launch"))
        self.assertAlmostEqual(slack["at"], T0 + 1)
        self.assertEqual(self.run_once(), {"sent": 0, "refused": 0})  # nothing new: nothing sent
        # A resend (lost answer) lands on the same row: no duplicate.
        self.mac.db.execute("DELETE FROM forwarded WHERE item_id=?", (a,))
        self.assertEqual(self.run_once()["sent"], 1)
        self.assertEqual(len(self.hub.s.items("all")), 2)

    def test_unreachable_hub_marks_nothing_and_catches_up_later(self):
        self.mac.upsert(item(1))
        self.mac.upsert(item(2))
        live = self.hub.url
        self.hub.close()
        with self.assertRaises(forward.Unreachable):
            forward.run_once(self.mac, live, self.hub.tok, "mac")
        self.assertEqual(len(forward.pending(self.mac)), 2)
        self.hub = Hub()
        self.mac.upsert(item(3))
        self.assertEqual(forward.run_once(self.mac, self.hub.url, self.hub.tok, "mac")["sent"], 3)
        self.assertEqual([i["body"] for i in self.hub.s.items()], ["msg 3", "msg 2", "msg 1"])

    def test_wrong_token_is_unreachable_not_refused(self):
        self.mac.upsert(item(1))
        with self.assertRaises(forward.Unreachable):
            forward.run_once(self.mac, self.hub.url, "pile_wrong", "mac")
        self.assertEqual(self.hub.s.items("all"), [])
        self.assertEqual(len(forward.pending(self.mac)), 1)  # still waiting, not dropped

    def test_refreshed_item_goes_again_and_reopens_on_the_hub(self):
        self.mac.upsert(item(1))
        self.run_once()
        hub_id = self.hub.s.items()[0]["id"]
        actions.done(self.hub.s, [hub_id])
        self.mac.upsert(item(1, at=T0 + 500, body="newer"))
        self.assertEqual(self.run_once()["sent"], 1)
        it = self.hub.s.get(hub_id)
        self.assertEqual((it["state"], it["body"]), ("new", "newer"))

    def test_skip_done_and_history_stay_home(self):
        old = store.Store(os.path.join(tempfile.mkdtemp(), "old.db"))
        old.upsert(item(1, at=T0))  # collected before forwarding began
        forward.setup(old, 0)
        new = old.upsert(item(2, at=T0 + 2))
        old.upsert(item(3, source="github", app="github"))
        self.assertEqual([i["id"] for i in forward.pending(old, skip=["github"])], [new])
        actions.done(old, [new])
        self.assertEqual(forward.pending(old, skip=["github"]), [])

    def test_link_only_the_sender_can_open_is_dropped_not_refused(self):
        self.mac.upsert(item(1, app="com.example.Thing", url="bundle:com.example.Thing"))
        self.assertEqual(self.run_once(), {"sent": 1, "refused": 0})
        self.assertEqual(self.hub.s.items()[0]["url"], "")

    def test_a_bad_item_is_skipped_not_retried_forever(self):
        def send(url, tok, body):
            if body["id"] == "u1":
                raise ValueError("hub refused it (400)")
            return forward.post(url, tok, body)
        self.mac.upsert(item(1))
        self.mac.upsert(item(2))
        self.assertEqual(self.run_once(send=send), {"sent": 1, "refused": 1})
        self.assertEqual(forward.pending(self.mac), [])

    def test_login_page_is_not_the_hub(self):
        # Cloudflare Access answers 200 with HTML; that must not count as delivered.
        import http.server

        class Page(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>sign in</html>")
        httpd = http.server.HTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            self.mac.upsert(item(1))
            with self.assertRaises(forward.Unreachable):
                forward.run_once(self.mac, "http://127.0.0.1:%d/" % httpd.server_address[1], "t", "mac")
            self.assertEqual(len(forward.pending(self.mac)), 1)
        finally:
            httpd.shutdown()
            httpd.server_close()


class OriginIngestTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")

    def test_origin_must_be_a_plain_name(self):
        with self.assertRaises(ValueError):
            webhook.ingest(self.s, {"origin": "Mac Book", "source": "macos", "id": "1", "app": "x", "at": T0})
        with self.assertRaises(ValueError):
            webhook.ingest(self.s, {"origin": "mac", "source": "macos", "app": "x", "at": T0})  # no id
        r = webhook.ingest(self.s, {"origin": "mac", "source": "macos", "id": "1", "app": "x", "at": T0,
                                    "url": "javascript:alert(1)"})
        self.assertEqual(self.s.get(r["id"])["url"], "")

    def test_plain_webhook_unchanged(self):
        r = webhook.ingest(self.s, {"app": "Deploys", "title": "prod is green"})
        it = self.s.get(r["id"])
        self.assertEqual((it["source"], it["app"]), ("webhook", "hook:deploys"))


class TokenTest(unittest.TestCase):
    def test_env_then_file(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "hub.token")
        with open(f, "w") as fh:
            fh.write("pile_fromfile\n")
        self.assertEqual(forward.token({"PILE_HUB_TOKEN_FILE": f}), "pile_fromfile")
        self.assertEqual(forward.token({"PILE_HUB_TOKEN_FILE": f, "PILE_HUB_TOKEN": "pile_env"}), "pile_env")
        self.assertEqual(forward.token({"PILE_HUB_TOKEN_FILE": os.path.join(d, "none")}), "")


if __name__ == "__main__":
    unittest.main()
