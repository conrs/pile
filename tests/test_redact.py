import json
import os
import tempfile
import unittest

from pile import demo, mcp, redact, store


class RedactTextTest(unittest.TestCase):
    def setUp(self):
        self.r = redact.Redactor(store.Store(":memory:"))

    def test_secrets_go(self):
        cases = {
            "Your code is 482913": "Your code is [code]",
            "Use verification code 4829-13 to sign in": "Use verification code [code] to sign in",
            "482913 is your login code": "[code] is your login code",
            "key sk-proj-AbCdEf1234567890XyZ and ghp_abcdefghijklmnopqrstuvwxyz0123": "key [secret] and [secret]",
            "slack xoxb-1234567890-abcdefghij": "slack [secret]",
            "aws AKIAIOSFODNN7EXAMPLE": "aws [secret]",
            "password: hunter2!": "password: [secret]",
            "card 4111 1111 1111 1111 exp 12/29": "card [card] exp 12/29",
            "IBAN GB82 WEST 1234 5698 7654 32": "IBAN [iban]",
            "token dGhpcyBpcyBhIHZlcnkgbG9uZyBzZWNyZXQgdG9rZW4x": "token [secret]",
        }
        for raw, want in cases.items():
            self.assertEqual(self.r.text(raw), want, raw)

    def test_controls_untouched(self):
        for s in ("3 PRs need review", "Design review in 15 minutes", "Bump lodash from 4.17.20 to 4.17.21",
                  "Fix race in session refresh (#412)", "Meeting at 2pm on 2026-11-05", "card 4111 1111 1111 1112",
                  "order 1234567890123", "acme/web#412", "the code review went fine"):
            self.assertEqual(self.r.text(s), s, s)

    def test_contacts_and_names_become_stable_pseudonyms(self):
        a = self.r.text("Call Dana Whitfield at (415) 555-0100 or dana@example.com", names=["Dana Whitfield"])
        self.assertNotIn("Dana", a)
        self.assertNotIn("555", a)
        self.assertNotIn("example.com", a)
        b = self.r.text("Dana said hi from +1 415 555 0100", names=["Dana Whitfield"])
        p = self.r.pseudonym("Dana Whitfield")
        self.assertIn(p, a)
        self.assertIn(p, b)  # first name alone maps to the same person
        self.assertEqual(self.r.pseudonym("415 555 0100".replace(" ", ""), "Phone"), self.r.pseudonym("4155550100", "Phone"))
        self.assertEqual(self.r.unmap(p), "Dana Whitfield")
        self.assertEqual(self.r.text("meet at 1600 Pennsylvania Ave, then lunch"), "meet at [address], then lunch")

    def test_allow_list(self):
        r = redact.Redactor(store.Store(":memory:"), allow=["Matt"])
        self.assertEqual(r.text("Matt asked", names=["Matt"]), "Matt asked")

    def test_key_is_stable_across_runs(self):
        path = os.path.join(tempfile.mkdtemp(), "inbox.db")
        a = redact.Redactor(store.Store(path)).pseudonym("Sam")
        b = redact.Redactor(store.Store(path)).pseudonym("Sam")
        self.assertEqual(a, b)
        self.assertEqual(oct(os.stat(os.path.join(os.path.dirname(path), "redact.key")).st_mode & 0o777), "0o600")


class MCPRedactTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")
        demo.seed(self.s)
        mcp.REDACTOR = redact.Redactor(self.s)

    def tearDown(self):
        mcp.REDACTOR = None

    def text(self, name, **args):
        return json.loads(mcp.call_text(self.s, name, args)) if hasattr(mcp, "call_text") else mcp.call(self.s, name, args)

    def test_agent_sees_pseudonyms_and_can_still_act(self):
        out = mcp.call(self.s, "list_inbox", {"limit": 100})
        blob = json.dumps(out)
        for real in ("Priya Raman", "Dana Whitfield", "Sam Okafor", "Robin Park"):
            self.assertNotIn(real, blob)
        self.assertIn("acme/web", blob)  # public repo names stay
        priya = next(i for i in out["items"] if i["app"] == "com.tinyspeck.slackmacgap" and i["sender"].startswith("Person-"))
        self.assertEqual(redact.Redactor(self.s).unmap(priya["sender"]), "Priya Raman")
        mcp.call(self.s, "mark_done", {"ids": [priya["id"]]})
        self.assertEqual(self.s.get(priya["id"])["state"], "done")
        # Rules can be set with the pseudonymous thread key the agent was shown.
        r = mcp.call(self.s, "set_rule", {"kind": "label", "value": "noise", "match": {"app": priya["app"], "sender": priya["thread"]}})
        self.assertTrue(r["sender"].startswith("Thread-"))
        real = self.s.db.execute("SELECT sender FROM rules").fetchone()[0]
        self.assertFalse(real.startswith("Thread-"))
        hist = json.dumps(mcp.call(self.s, "history", {}))
        self.assertNotIn("Priya", hist)


class CrossMentionTest(unittest.TestCase):
    def test_a_known_person_mentioned_elsewhere_is_caught(self):
        s = store.Store(":memory:")
        demo.seed(s)
        r = redact.Redactor(s)
        item = {"sender": "Alex Chen", "app": "com.tinyspeck.slackmacgap", "app_name": "Slack", "title": "",
                "body": "Priya Raman said the calendar invite is out", "thread": "x"}
        out = r.item(item)
        self.assertNotIn("Priya", out["body"])
        self.assertIn("calendar invite", out["body"])


class NoOverreachTest(unittest.TestCase):
    def test_newsletter_names_and_common_words_survive(self):
        s = store.Store(":memory:")
        demo.seed(s)
        r = redact.Redactor(s)
        out = r.item({"sender": "Priya Raman", "app": "com.tinyspeck.slackmacgap", "app_name": "Slack", "title": "#launch",
                      "body": "Can you sanity-check the pricing page? Will is out, will you?", "thread": "x"})
        self.assertIn("the pricing page", out["body"])
        self.assertIn("will you", out["body"])
