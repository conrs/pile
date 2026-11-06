import os
import tempfile
import unittest

from pile import actions, demo, mcp, rules, store


class UndoTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "u.db")
        self.s = store.Store(self.path)
        demo.seed(self.s)
        self.items = self.s.items()
        self.ids = [i["id"] for i in self.items]

    def state(self, s=None):
        s = s or self.s
        items = {r["id"]: (r["state"], r["label"]) for r in s.db.execute("SELECT id, state, label FROM items")}
        rs = sorted((r["kind"], r["app"], r["sender"], r["value"]) for r in s.db.execute("SELECT * FROM rules"))
        return items, rs

    def test_unwinds_every_kind_in_order_across_a_restart(self):
        snaps = [self.state()]
        slack = next(i for i in self.items if i["app"] == "com.tinyspeck.slackmacgap")
        discord = next(i for i in self.items if i["app"] == "com.hnc.Discord")
        steps = [
            lambda: actions.done(self.s, self.ids[:3]),
            lambda: actions.snooze(self.s, self.ids[3:5], "1h"),
            lambda: actions.teach_label(self.s, slack["id"], "noise"),
            lambda: actions.teach_label(self.s, slack["id"], "needs-you"),   # replaces the noise rule
            lambda: actions.fix_link(self.s, slack["id"], "https://acme.slack.com/archives/launch"),
            lambda: actions.mute_app(self.s, discord["id"]),
            lambda: actions.group(self.s, self.ids[1], self.ids[8]),
            lambda: actions.reopen(self.s, self.ids[:1]),
        ]
        for step in steps:
            step()
            snaps.append(self.state())
        actions.open_item(self.s, self.ids[0])  # not a change: not on the stack
        self.s.db.close()
        self.s = store.Store(self.path)  # restart
        self.assertEqual(self.s.undo_depth(), len(steps))
        for want in reversed(snaps[:-1]):
            r = self.s.undo()
            self.assertEqual(len(r["undid"]), 1)
            self.assertEqual(self.state(), want)
        self.assertEqual(self.s.undo()["undid"], [])
        # And forward again.
        self.assertEqual(self.s.redo(len(steps))["redoable"], 0)
        self.assertEqual(self.state(), snaps[-1])

    def test_new_change_ends_redo(self):
        actions.done(self.s, self.ids[:1])
        self.s.undo()
        self.assertEqual(self.s.redo_depth(), 1)
        actions.done(self.s, self.ids[2:3])
        self.assertEqual(self.s.redo_depth(), 0)
        self.assertEqual(self.s.redo()["redid"], [])

    def test_description_and_mcp(self):
        actions.done(self.s, self.ids[:3])
        actions.done(self.s, self.ids[5:6])
        r = self.s.undo()
        self.assertTrue(r["undid"][0].startswith("done · "))
        self.assertEqual(r["remaining"], 1)
        out = mcp.call(self.s, "undo", {"steps": 5})
        self.assertEqual(len(out["undid"]), 1)
        self.assertIn("+2", out["undid"][0])
        self.assertEqual(len(self.s.items()), len(self.ids))
        self.assertEqual(rules.all_rules(self.s), [])


if __name__ == "__main__":
    unittest.main()


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")
        demo.seed(self.s)
        self.items = self.s.items()

    def snap(self):
        return (sorted((r["id"], r["state"], r["label"]) for r in self.s.db.execute("SELECT * FROM items")),
                self.s.db.execute("SELECT COUNT(*) FROM rules").fetchone()[0])

    def test_batch_label_is_one_undo(self):
        before = self.snap()
        ids = [i["id"] for i in self.items[:4]]
        r = actions.teach_labels(self.s, ids, "noise")
        self.assertEqual(len(r["rules"]), 4)
        self.assertTrue(all(self.s.get(i)["label"] == "noise" for i in ids))
        self.s.undo()
        self.assertEqual(self.snap(), before)

    def test_batch_mute_is_one_undo(self):
        before = self.snap()
        apps = {}
        for i in self.items:
            apps.setdefault(i["app"], i["id"])
        ids = list(apps.values())[:3]
        self.assertEqual(len(actions.mute_apps(self.s, ids)["rules"]), 3)
        self.s.undo()
        self.assertEqual(self.snap(), before)

    def test_combine_into_newest_and_undo(self):
        a, b, c = self.items[0], self.items[3], self.items[8]
        r = actions.combine(self.s, [b["id"], c["id"], a["id"]])
        self.assertEqual(r["thread"], a["thread"])  # items come newest first
        self.assertEqual({self.s.get(x["id"])["thread"] for x in (a, b, c)}, {a["thread"]})
        self.assertEqual(self.s.undo_depth(), 1)
        self.s.undo()
        self.assertEqual(self.s.get(c["id"])["thread"], c["thread"])
        with self.assertRaises(ValueError):
            actions.combine(self.s, [a["id"]])
