import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

from pile import actions, server, store
from pile.collectors import messages

from .fixtures import make_chat_db

NOW = time.time()


class OpenEndpointTest(unittest.TestCase):
    def setUp(self):
        os.environ["PILE_NO_OPEN"] = "1"
        self.s = store.Store(":memory:")
        self.gh = self.s.upsert({"source": "github", "ext_id": "1", "app": "github", "app_name": "GitHub",
                                 "thread": "github|1", "sender": "acme/web", "title": "Fix race", "at": NOW,
                                 "url": "https://github.com/acme/web/pull/412"})
        self.slack = self.s.upsert({"source": "macos", "ext_id": "2", "app": "com.tinyspeck.slackmacgap",
                                    "app_name": "Slack", "thread": "s|x", "sender": "Priya", "at": NOW,
                                    "url": "slack://open"})
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.s, threading.Lock(), {}))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/api/open" % self.httpd.server_address[1],
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_web_link_opened_by_the_page_is_not_opened_twice(self):
        r = self.post({"id": self.gh, "browser_opened": True})
        self.assertEqual((r["link"], r["result"]), ("https://github.com/acme/web/pull/412", "opened in the browser"))
        self.assertEqual(self.s.history()[0]["action"], "open")

    def test_app_links_open_on_the_server(self):
        r = self.post({"id": self.slack, "browser_opened": True})  # not http: the server opens it regardless
        self.assertEqual(r["result"], "would open slack://open")


class MessagesResolveTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.db = os.path.join(d, "chat.db")
        make_chat_db(self.db, [
            ("iMessage;-;+15551230001", None, ["+15551230001"], [("+15551230001", "can you send the deck?", 3)]),
            ("iMessage;-;+15551230002", None, ["+15551230002"], [("+15551230002", "lunch?", 2)]),
            ("chat123", "Family", ["+15551230003", "+15551230004"], [("+15551230004", "dinner at 7?", 5)]),
        ], NOW)

    def item(self, sender, title, body, mins):
        return {"sender": sender, "title": title, "body": body, "at": NOW - mins * 60}

    def test_matches_text_and_time(self):
        link, why, _ = messages.resolve(self.item("Priya", "", "can you send the deck?", 3), self.db)
        self.assertEqual(link, "sms:+15551230001")
        self.assertIn("text", why)

    def test_group_by_name(self):
        link, why, variants = messages.resolve(self.item("Jo", "Family", "dinner at 7?", 5), self.db)
        self.assertEqual(link, "messages://open?message-guid=chat123-m0")
        self.assertIn("bundle:com.apple.MobileSMS", variants)

    def test_nothing_near(self):
        link, why, _ = messages.resolve(self.item("Nobody", "", "hello", 600), self.db)
        self.assertIsNone(link)

    def test_open_test_resolves_and_keeps_it(self):
        from unittest import mock
        s = store.Store(":memory:")
        iid = s.upsert({"source": "macos", "ext_id": "x", "app": "com.apple.MobileSMS", "app_name": "Messages",
                        "thread": "m|Priya", "sender": "Priya", "body": "can you send the deck?", "at": NOW - 180,
                        "url": "sms:"})
        with mock.patch.object(messages, "default_db", return_value=self.db):
            r = actions.open_test(s, iid)
        self.assertEqual(r["link"], "sms:+15551230001")
        self.assertEqual(s.get(iid)["url"], "sms:+15551230001")  # kept for next time
        from pile import rules
        rules.learn_link(s, s.get(iid), "imessage://+15551230001")
        self.assertEqual(s.get(iid)["link"], "imessage://+15551230001")  # learning still wins


if __name__ == "__main__":
    unittest.main()
