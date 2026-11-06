import unittest

from pile import actions, rules, store


def slack(s, sender="Priya Raman", title="#launch", ext="1", at=1.0):
    iid = s.upsert({"source": "macos", "ext_id": ext, "app": "com.tinyspeck.slackmacgap", "app_name": "Slack",
                    "thread": "slack|" + title, "sender": sender, "title": title, "body": "hi", "at": at,
                    "url": "slack://open"})
    return s.get(iid)


class GeneraliseTest(unittest.TestCase):
    def item(self, **kw):
        base = {"sender": "Priya Raman", "title": "#launch", "ext_id": "abc"}
        base.update(kw)
        return base

    def test_slug_of_title_becomes_placeholder(self):
        t, generic = rules.generalise("https://chat.example.com/acme/channels/launch", self.item())
        self.assertEqual(t, "https://chat.example.com/acme/channels/{title:slug}")
        self.assertTrue(generic)

    def test_url_encoded_sender(self):
        t, generic = rules.generalise("https://mail.example.com/search?from=Priya%20Raman", self.item())
        self.assertEqual(t, "https://mail.example.com/search?from={sender:q}")
        self.assertTrue(generic)

    def test_host_is_never_generalised(self):
        t, generic = rules.generalise("https://launch.example.com/", self.item())
        self.assertEqual((t, generic), ("https://launch.example.com/", False))

    def test_no_partial_word_matches(self):
        t, generic = rules.generalise("https://x.example.com/relaunched", self.item())
        self.assertFalse(generic)

    def test_opaque_link_stays_specific(self):
        t, generic = rules.generalise("slack://channel?team=T01&id=C0123", self.item())
        self.assertEqual((t, generic), ("slack://channel?team=T01&id=C0123", False))

    def test_render_roundtrip_and_missing_field(self):
        self.assertEqual(rules.render("https://h/{title:slug}?q={sender:q}", {"title": "#Big News", "sender": "A B"}),
                         "https://h/big-news?q=A%20B")
        self.assertIsNone(rules.render("https://h/{title:slug}", {"title": ""}))

    def test_bundle_id_paste(self):
        self.assertEqual(rules.normalise_target("com.example.App"), "bundle:com.example.App")
        self.assertEqual(rules.normalise_target("https://x.example.com"), "https://x.example.com")


class LearnedLinkTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")

    def test_generic_fix_covers_the_whole_app(self):
        a = slack(self.s)
        b = slack(self.s, sender="Sam", title="#design", ext="2")
        r = actions.fix_link(self.s, a["id"], "https://acme.slack.com/archives/launch")
        self.assertIsNone(r["rule"]["sender"])
        self.assertEqual(self.s.get(b["id"])["link"], "https://acme.slack.com/archives/design")
        self.assertTrue(self.s.get(b["id"])["link_learned"])

    def test_opaque_fix_only_covers_that_thread(self):
        a = slack(self.s)
        b = slack(self.s, sender="Sam", title="#design", ext="2")
        actions.fix_link(self.s, a["id"], "slack://channel?team=T01&id=C0123")
        self.assertEqual(self.s.get(a["id"])["link"], "slack://channel?team=T01&id=C0123")
        self.assertEqual(self.s.get(b["id"])["link"], "slack://open")
        self.assertFalse(self.s.get(b["id"])["link_learned"])
        # A later message in the same thread uses it too.
        c = slack(self.s, sender="Priya Raman", ext="3")
        self.assertEqual(c["link"], "slack://channel?team=T01&id=C0123")

    def test_specific_beats_generic_and_delete_forgets(self):
        a = slack(self.s)
        actions.fix_link(self.s, a["id"], "https://acme.slack.com/archives/launch")
        specific = actions.fix_link(self.s, a["id"], "slack://channel?team=T01&id=C0123")["rule"]
        self.assertEqual(self.s.get(a["id"])["link"], "slack://channel?team=T01&id=C0123")
        rules.delete(self.s, specific["id"])
        self.assertEqual(self.s.get(a["id"])["link"], "https://acme.slack.com/archives/launch")

    def test_other_apps_untouched(self):
        a = slack(self.s)
        actions.fix_link(self.s, a["id"], "https://acme.slack.com/archives/launch")
        iid = self.s.upsert({"source": "macos", "ext_id": "m", "app": "com.apple.MobileSMS", "app_name": "Messages",
                             "thread": "sms|Sam", "sender": "Sam", "title": "#launch", "body": "", "at": 1.0,
                             "url": "sms:"})
        self.assertEqual(self.s.get(iid)["link"], "sms:")


class LabelAndMuteTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")

    def test_not_important_sticks_to_sender(self):
        a = slack(self.s)
        actions.teach_label(self.s, a["id"], "noise")
        self.assertEqual(self.s.get(a["id"])["label"], "noise")
        later = slack(self.s, ext="9")  # same thread, new message
        self.assertEqual(later["label"], "noise")
        other = slack(self.s, sender="Sam", title="#design", ext="10")
        self.assertIsNone(other["label"])

    def test_sender_rule_beats_app_rule(self):
        a = slack(self.s)
        actions.teach_label(self.s, a["id"], "noise", scope="app")
        actions.teach_label(self.s, a["id"], "needs-you")
        self.assertEqual(slack(self.s, ext="2")["label"], "needs-you")
        self.assertEqual(slack(self.s, sender="Sam", title="#random", ext="3")["label"], "noise")

    def test_triage_applies_rules_without_a_model(self):
        from pile import triage
        a = slack(self.s)
        rules.add(self.s, "label", "fyi", app="com.tinyspeck.slackmacgap")
        self.s.set_label(a["id"], None, None)
        self.assertEqual(triage.run(self.s)["by_rule"], 1)
        self.assertEqual(self.s.get(a["id"])["label"], "fyi")

    def test_mute_clears_and_skips(self):
        a = slack(self.s)
        actions.mute_app(self.s, a["id"])
        self.assertEqual(self.s.items(), [])
        self.assertIsNone(self.s.upsert({"source": "macos", "ext_id": "x", "app": "com.tinyspeck.slackmacgap",
                                         "app_name": "Slack", "sender": "Sam", "at": 2.0}))

    def test_bad_rules_rejected(self):
        with self.assertRaises(ValueError):
            rules.add(self.s, "label", "urgent")
        with self.assertRaises(ValueError):
            rules.add(self.s, "mute")


if __name__ == "__main__":
    unittest.main()
