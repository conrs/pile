import os
import tempfile
import unittest

from pile import actions, store, writeback


def item(source, ext, n):
    return {"source": source, "ext_id": ext, "app": "x", "app_name": "X", "thread": "t%d" % n, "sender": "s",
            "title": "", "body": "", "at": 1000.0 + n, "url": ""}


class WritebackTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(os.path.join(tempfile.mkdtemp(), "w.db"))
        self.gh = self.s.upsert(item("github", "123", 1))
        self.mail = self.s.upsert(item("imap:work", "<a@b>", 2))
        self.other = self.s.upsert(item("macos", "u1", 3))
        self.search = self.s.upsert(item("github", "search:9", 4))
        self.seen = []
        self.s.on_change = lambda c: self.seen.append(writeback.plan(self.s, c))

    def test_done_undo_redo_tell_the_sources(self):
        actions.done(self.s, [self.gh, self.mail, self.other, self.search])
        self.assertEqual(sorted(self.seen[-1]), [("github", "123", True), ("imap:work", "<a@b>", True)])
        actions.undo(self.s)
        self.assertEqual(self.seen[-1], [("imap:work", "<a@b>", False)])
        # GitHub can't be unread again: kept out of the next sync's "read elsewhere" sweep instead
        self.assertEqual(self.s.get(self.gh)["tag"], "earlier")
        self.s.close_missing("github", [])
        self.assertEqual(self.s.get(self.gh)["state"], "new")
        actions.redo(self.s)
        self.assertEqual(sorted(self.seen[-1]), [("github", "123", True), ("imap:work", "<a@b>", True)])

    def test_snooze_and_labels_stay_local(self):
        actions.snooze(self.s, [self.gh, self.mail], "1h")
        self.assertEqual(self.seen[-1], [])
        actions.done(self.s, [self.mail])  # snoozed -> done still counts
        self.assertEqual(self.seen[-1], [("imap:work", "<a@b>", True)])

    def test_off_unless_asked(self):
        self.assertFalse(writeback.enabled({}))
        self.assertTrue(writeback.enabled({"PILE_WRITEBACK": "1"}))
        s = store.Store(os.path.join(tempfile.mkdtemp(), "x.db"))
        old = os.environ.pop("PILE_WRITEBACK", None)
        try:
            writeback.attach(s)
            self.assertIsNone(s.on_change)
        finally:
            if old is not None:
                os.environ["PILE_WRITEBACK"] = old


if __name__ == "__main__":
    unittest.main()
