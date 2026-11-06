import io
import json
import time
import unittest
from datetime import datetime

from pile import actions, demo, mcp, store
from pile.collectors import imap


def rpc(s, *msgs):
    out = io.StringIO()
    mcp.serve(s, io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def tool(s, name, **args):
    r = rpc(s, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})[0]
    return r["result"]


class MCPTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(":memory:")
        demo.seed(self.s)

    def test_handshake_and_tools(self):
        init, lst = rpc(self.s,
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        names = {t["name"] for t in lst["result"]["tools"]}
        self.assertTrue({"list_inbox", "get_item", "mark_done", "snooze", "open_item",
                         "fix_link", "set_rule", "list_rules"} <= names)

    def test_triage_flow(self):
        res = json.loads(tool(self.s, "list_inbox", label="needs-you", limit=3)["content"][0]["text"])
        self.assertEqual(res["count"], 3)
        ids = [i["id"] for i in res["items"]]
        tool(self.s, "mark_done", ids=ids[:2])
        tool(self.s, "snooze", ids=ids[2:], until="tomorrow")
        self.assertEqual(self.s.get(ids[0])["state"], "done")
        self.assertEqual(self.s.get(ids[2])["state"], "snoozed")

    def test_agent_can_teach(self):
        item = self.s.items()[0]
        r = json.loads(tool(self.s, "fix_link", id=item["id"], url="slack://channel?id=C1")["content"][0]["text"])
        self.assertEqual(r["now_opens"], "slack://channel?id=C1")
        tool(self.s, "set_rule", kind="mute", match={"app": "com.hnc.Discord"})
        self.assertFalse([i for i in self.s.items() if i["app"] == "com.hnc.Discord"])
        rules_ = json.loads(tool(self.s, "list_rules")["content"][0]["text"])["rules"]
        self.assertEqual({r["kind"] for r in rules_}, {"link", "mute"})

    def test_errors(self):
        self.assertTrue(tool(self.s, "get_item", id=99999)["isError"])
        r = rpc(self.s, {"jsonrpc": "2.0", "id": 5, "method": "nope"})[0]
        self.assertEqual(r["error"]["code"], -32601)


class MiscTest(unittest.TestCase):
    def test_parse_until(self):
        now = datetime(2026, 11, 5, 19, 30).timestamp()
        self.assertEqual(actions.parse_until("1h", now), now + 3600)
        self.assertEqual(datetime.fromtimestamp(actions.parse_until("tomorrow", now)), datetime(2026, 11, 6, 9, 0))
        self.assertEqual(datetime.fromtimestamp(actions.parse_until("tonight", now)), datetime(2026, 11, 6, 18, 0))

    def test_snooze_wakes(self):
        s = store.Store(":memory:")
        demo.seed(s)
        iid = s.items()[0]["id"]
        s.set_state([iid], "snoozed", time.time() - 1)
        self.assertEqual(s.get(iid)["state"], "snoozed")
        s.items()
        self.assertEqual(s.get(iid)["state"], "new")

    def test_imap_message(self):
        raw = (b"From: =?utf-8?q?Dana_Whitfield?= <dana@example.com>\r\nSubject: Re: Thursday\r\n"
               b"Date: Thu, 05 Nov 2026 18:00:00 -0800\r\nMessage-ID: <abc.123@mail.example.com>\r\n"
               b"Content-Type: text/plain; charset=utf-8\r\n\r\nThursday 2pm works.\r\n")
        item = imap.to_item(raw, "imap.gmail.com")
        self.assertEqual((item["sender"], item["title"], item["body"]), ("Dana Whitfield", "Re: Thursday", "Thursday 2pm works."))
        self.assertEqual(item["url"], "https://mail.google.com/mail/u/0/#search/rfc822msgid:abc.123@mail.example.com")
        self.assertEqual(imap.to_item(raw, "imap.example.com")["url"], "message://%3Cabc.123@mail.example.com%3E")


if __name__ == "__main__":
    unittest.main()
