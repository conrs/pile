import os
import tempfile
import unittest

from pile import store
from pile.collectors import github, macos

from .fixtures import gh, make_nc_db

T0 = 1793900000.0


class MacOSTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.nc = os.path.join(self.dir, "db")
        make_nc_db(self.nc, [
            ("com.tinyspeck.slackmacgap", "Priya Raman", "#launch", "pricing page?", T0),
            ("com.apple.MobileSMS", "Sam", None, "running late", T0 + 60),
            ("_WEB_CENTER_:web.calendar.example.com", "Standup", None, "in 5 minutes", T0 + 120),
        ])
        self.s = store.Store(os.path.join(self.dir, "inbox.db"))

    def test_reads_records_with_deep_links(self):
        self.assertEqual(macos.collect(self.s, self.nc), 3)
        items = {i["app"]: i for i in self.s.items()}
        slack = items["com.tinyspeck.slackmacgap"]
        self.assertEqual((slack["sender"], slack["title"], slack["body"]), ("Priya Raman", "#launch", "pricing page?"))
        self.assertEqual(slack["app_name"], "Slack")
        self.assertEqual(slack["link"], "slack://open")
        self.assertAlmostEqual(slack["at"], T0)
        self.assertEqual(items["com.apple.MobileSMS"]["link"], "sms:")
        web = items["_WEB_CENTER_:web.calendar.example.com"]
        self.assertEqual((web["app_name"], web["link"]), ("calendar.example.com", "https://calendar.example.com"))

    def test_watermark_skips_seen_records(self):
        macos.collect(self.s, self.nc)
        self.assertEqual(macos.collect(self.s, self.nc), 0)
        self.assertEqual(self.s.get_meta("macos.rec_id"), "3")

    def test_unknown_app_opens_the_app(self):
        make_nc_db(os.path.join(self.dir, "db2"), [("com.example.Thing", "Hi", None, "x", T0)])
        macos.collect(self.s, os.path.join(self.dir, "db2"))
        self.assertEqual(self.s.items()[0]["link"], "bundle:com.example.Thing")

    def test_garbage_plist_is_skipped(self):
        self.assertIsNone(macos.parse_record(1, "x", b"not a plist", 0))


class GitHubTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")

    def test_api_urls_become_pages(self):
        cases = [
            (gh(1, "PullRequest", "https://api.github.com/repos/acme/web/pulls/412", "Fix race"),
             "https://github.com/acme/web/pull/412"),
            (gh(2, "Issue", "https://api.github.com/repos/acme/web/issues/97", "Bug",
                comment="https://api.github.com/repos/acme/web/issues/comments/555"),
             "https://github.com/acme/web/issues/97#issuecomment-555"),
            (gh(3, "Commit", "https://api.github.com/repos/acme/web/commits/abc123", "c"),
             "https://github.com/acme/web/commit/abc123"),
            (gh(4, "CheckSuite", None, "CI failed"), "https://github.com/acme/web/actions"),
            (gh(5, "Release", "https://api.github.com/repos/acme/web/releases/9", "v1"),
             "https://github.com/acme/web/releases"),
        ]
        for note, want in cases:
            self.assertEqual(github.html_url(note), want)

    def test_ingest_and_reconcile(self):
        github.collect(self.s, [gh(1, "PullRequest", "https://api.github.com/repos/acme/web/pulls/1", "A"),
                                gh(2, "Issue", "https://api.github.com/repos/acme/web/issues/2", "B")])
        self.assertEqual(len(self.s.items()), 2)
        # Read on github.com: it drops out of the list, so pile clears it too.
        github.collect(self.s, [gh(1, "PullRequest", "https://api.github.com/repos/acme/web/pulls/1", "A")])
        self.assertEqual([i["title"] for i in self.s.items()], ["A"])

    def test_new_activity_reopens(self):
        note = gh(1, "PullRequest", "https://api.github.com/repos/acme/web/pulls/1", "A")
        github.collect(self.s, [note])
        self.s.set_state([self.s.items()[0]["id"]], "done")
        github.collect(self.s, [note])
        self.assertEqual(self.s.items(), [])
        github.collect(self.s, [dict(note, updated_at="2026-11-05T19:00:00Z")])
        self.assertEqual(len(self.s.items()), 1)


if __name__ == "__main__":
    unittest.main()
