import email
import json
import os
import tempfile
import time
import unittest

from pile import actions, cli, rules, store
from pile.collectors import github, imap, macos, messages

from .fixtures import gh, make_chat_db, make_nc_db

NOW = time.time()


class MessagesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "chat.db")
        make_chat_db(self.db, [
            ("iMessage;-;+15551230001", None, ["+15551230001"],
             [(None, "you around?", 600), ("+15551230001", "can you send the deck?", 300)]),          # theirs last
            ("iMessage;-;+15551230002", None, ["+15551230002"],
             [("+15551230002", "lunch?", 600), (None, "yes!", 500)]),                               # I replied
            ("iMessage;-;sam@example.com", None, ["sam@example.com"],
             [("sam@example.com", "photos from Sunday", 400, True)]),                                # text in attributedBody
            ("chat123", "Family", ["+15551230003", "+15551230004"],
             [("+15551230003", "dinner at 7?", 200)]),                                              # group
            ("iMessage;-;+15551230005", None, ["+15551230005"],
             [("+15551230005", "just now", 30)]),                                                   # too fresh
            ("iMessage;-;+15551230006", None, ["+15551230006"],
             [("+15551230006", "old news", 60 * 24 * 60)]),                                         # older than --days
        ], NOW)
        self.s = store.Store(":memory:")

    def test_only_conversations_waiting_on_you(self):
        self.assertEqual(messages.backfill(self.s, 30, messages_db=self.db), 3)
        items = {i["sender"]: i for i in self.s.items()}
        self.assertEqual(set(items), {"+15551230001", "sam@example.com", "+15551230003"})
        one = items["+15551230001"]
        self.assertEqual((one["body"], one["tag"], one["link"]), ("can you send the deck?", "unanswered", "sms:+15551230001"))
        self.assertAlmostEqual(one["at"], NOW - 300 * 60, delta=2)
        self.assertEqual(items["sam@example.com"]["body"], "photos from Sunday")
        self.assertEqual(items["sam@example.com"]["link"], "imessage:sam@example.com")
        fam = items["+15551230003"]
        self.assertEqual((fam["title"], fam["link"]), ("Family", "bundle:com.apple.MobileSMS"))

    def test_rerun_is_idempotent(self):
        messages.backfill(self.s, 30, messages_db=self.db)
        messages.backfill(self.s, 30, messages_db=self.db)
        self.assertEqual(len(self.s.items()), 3)
        self.assertEqual(self.s.stats()["unanswered"], 3)

    def test_contact_names(self):
        self.assertEqual(messages.name_for("+1 (555) 123-0001", {"5551230001": "Priya"}), "Priya")
        self.assertEqual(messages.name_for("x@example.com", {}), "x@example.com")


class GitHubBackfillTest(unittest.TestCase):
    def test_read_notifications_and_searches(self):
        s = store.Store(":memory:")
        read = dict(gh(9, "Issue", "https://api.github.com/repos/acme/web/issues/9", "Old thread"), unread=False)
        search = {"id": 77, "title": "Add retries", "html_url": "https://github.com/acme/api/pull/5",
                  "repository_url": "https://api.github.com/repos/acme/api", "updated_at": "2026-11-01T10:00:00Z"}
        self.assertEqual(github.backfill(s, 30, notifications=[read], searches=[(search, "review requested from you")]), 2)
        tags = {i["title"]: i["tag"] for i in s.items()}
        self.assertEqual(tags, {"Old thread": "earlier", "Add retries": "unanswered"})
        # A normal collect doesn't clear backfilled items it no longer sees.
        github.collect(s, [])
        self.assertEqual(len(s.items()), 2)


class MailTest(unittest.TestCase):
    def test_people_not_lists(self):
        person = email.message_from_string("From: Dana <dana@example.com>\nSubject: hi\n\nyo")
        lst = email.message_from_string("From: News <news@example.com>\nList-Unsubscribe: <x>\nSubject: hi\n\nyo")
        robot = email.message_from_string("From: GitHub <noreply@github.com>\nSubject: hi\n\nyo")
        self.assertEqual([imap.is_person(m) for m in (person, lst, robot)], [True, False, False])


class ThreadsTest(unittest.TestCase):
    def test_notification_thread_ids_group_rows(self):
        d = tempfile.mkdtemp()
        nc = os.path.join(d, "db")
        make_nc_db(nc, [
            ("com.apple.MobileSMS", "Alex", "Family Chat", "dinner?", NOW - 60, "chat-family"),
            ("com.apple.MobileSMS", "Jo", "Family Chat", "7 works", NOW - 30, "chat-family"),
            ("com.apple.MobileSMS", "Jo", None, "unrelated", NOW, "chat-jo"),
        ])
        s = store.Store(":memory:")
        macos.collect(s, macos_db=nc)
        threads = [i["thread"] for i in s.items()]
        self.assertEqual(len(set(threads)), 2)
        self.assertEqual(threads.count("com.apple.MobileSMS|t:chat-family"), 2)

    def test_teach_and_forget_a_grouping(self):
        s = store.Store(":memory:")
        a = s.upsert({"source": "macos", "ext_id": "1", "app": "x", "app_name": "X", "thread": "x|Family", "sender": "Al", "at": 1.0})
        b = s.upsert({"source": "macos", "ext_id": "2", "app": "x", "app_name": "X", "thread": "x|Fam 2", "sender": "Jo", "at": 2.0})
        actions.group(s, a, b)
        self.assertEqual(s.get(b)["thread"], "x|Family")
        later = s.upsert({"source": "macos", "ext_id": "3", "app": "x", "app_name": "X", "thread": "x|Fam 2", "sender": "Jo", "at": 3.0})
        self.assertEqual(s.get(later)["thread"], "x|Family")  # learned for next time
        self.assertEqual([r["kind"] for r in rules.all_rules(s)], ["group"])
        actions.ungroup(s, a)
        self.assertEqual(s.get(b)["thread"], "x|Fam 2")

    def test_group_rule_needs_both_sides(self):
        with self.assertRaises(ValueError):
            rules.add(store.Store(":memory:"), "group", "", app="x", sender="x|a")


class HistoryAndReopenTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "p.db")
        self.s = store.Store(self.path)
        from pile import demo
        demo.seed(self.s)

    def test_actions_are_logged(self):
        ids = [i["id"] for i in self.s.items()][:3]
        actions.done(self.s, ids[:2])
        actions.snooze(self.s, ids[2:], "1h")
        actions.teach_label(self.s, ids[0], "noise")
        h = self.s.history()
        self.assertEqual([r["action"] for r in h[:3]], ["label", "snooze", "done"])
        self.assertEqual(h[2]["detail"]["ids"], ids[:2])
        self.assertEqual(h[0]["detail"]["label"], "noise")

    def test_reopen(self):
        ids = [i["id"] for i in self.s.items()]
        actions.done(self.s, ids[:5])
        self.s.db.execute("UPDATE items SET changed_at=? WHERE id=?", (time.time() - 3 * 86400, ids[0]))
        self.assertEqual(self.s.reopen(since=time.time() - 7200), 4)
        self.assertEqual(self.s.reopen(), 1)
        self.assertEqual(len(self.s.items()), len(ids))

    def test_cli_list_state_and_history(self):
        import io
        from contextlib import redirect_stdout
        actions.done(self.s, [self.s.items()[0]["id"]])
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["list", "--state", "done", "--db", self.path, "--json"])
        self.assertEqual(len(json.loads(out.getvalue())), 1)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["history", "--db", self.path, "--json"])
        self.assertEqual(json.loads(out.getvalue())[0]["action"], "done")
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["reopen", "--all", "--db", self.path])
        self.assertEqual(out.getvalue().strip(), "reopened 1")


if __name__ == "__main__":
    unittest.main()
