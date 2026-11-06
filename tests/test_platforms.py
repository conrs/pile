import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from pile import collectors, store
from pile.collectors import linux, webhook, windows

TOAST = ('<toast launch="slack://channel?team=T1&amp;id=C9" activationType="protocol"><visual>'
         '<binding template="ToastGeneric"><text>Priya Raman</text><text>pricing page?</text></binding></visual></toast>')
PLAIN = '<toast><visual><binding template="ToastGeneric"><text>Backup</text><text>Finished</text></binding></visual></toast>'


def make_wpn(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT, HandlerType TEXT);
      CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, Payload BLOB,
                                 ArrivalTime INTEGER, ExpiryTime INTEGER, Tag TEXT, "Group" TEXT);
    """)
    db.execute("INSERT INTO NotificationHandler VALUES (1, 'com.squirrel.slack.slack', 'app:desktop')")
    db.execute("INSERT INTO NotificationHandler VALUES (2, 'Contoso.Backup_8wekyb3d8bbwe!App', 'app:immersive')")
    ft = int((1793900000 + 11644473600) * 1e7)
    db.execute("INSERT INTO Notification(HandlerId, Type, Payload, ArrivalTime) VALUES (1, 'toast', ?, ?)", (TOAST.encode(), ft))
    db.execute("INSERT INTO Notification(HandlerId, Type, Payload, ArrivalTime) VALUES (2, 'toast', ?, ?)", (PLAIN.encode(), ft + 10**7))
    db.execute("INSERT INTO Notification(HandlerId, Type, Payload, ArrivalTime) VALUES (2, 'badge', '<badge value=\"3\"/>', ?)", (ft,))
    db.commit()
    db.close()


class WindowsTest(unittest.TestCase):
    def test_reads_toasts(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "wpndatabase.db")
        make_wpn(path)
        s = store.Store(":memory:")
        self.assertEqual(windows.collect(s, windows_db=path), 2)
        self.assertEqual(windows.collect(s, windows_db=path), 0)  # watermark
        items = {i["app_name"]: i for i in s.items()}
        slack = items["Slack"]
        self.assertEqual((slack["sender"], slack["body"]), ("Priya Raman", "pricing page?"))
        self.assertEqual(slack["link"], "slack://channel?team=T1&id=C9")  # the toast's own launch link
        self.assertAlmostEqual(slack["at"], 1793900000, places=0)
        self.assertEqual(items["Backup"]["link"], "shell:AppsFolder\\Contoso.Backup_8wekyb3d8bbwe!App")

    def test_fixture_path_runs_on_any_os(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "w.db")
        make_wpn(path)
        st = {m.NAME: ok for m, ok, _ in collectors.status({"windows_db": path})}
        self.assertTrue(st["windows"])


DBUS = '''signal time=1.0 sender=org.freedesktop.DBus -> destination=:1.9 serial=2 path=/org/freedesktop/DBus; interface=org.freedesktop.DBus; member=NameAcquired
   string ":1.9"
method call time=2.0 sender=:1.40 -> destination=:1.12 serial=9 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "Slack"
   uint32 0
   string "slack"
   string "Priya Raman"
   string "pricing page?
second line"
   array [
   ]
   array [
      dict entry(
         string "desktop-entry"
         variant             string "slack"
      )
   ]
   int32 -1
method call time=3.0 sender=:1.41 -> destination=:1.12 serial=10 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "KDE Connect"
   uint32 0
   string ""
   string "WhatsApp: Mom"
   string "<b>Did</b> you see the photos?"
   array [
   ]
'''


class LinuxTest(unittest.TestCase):
    def test_parse_notify_calls(self):
        calls = list(linux.parse(DBUS.splitlines(True)))
        self.assertEqual(calls, [("Slack", "Priya Raman", "pricing page?\nsecond line"),
                                 ("KDE Connect", "WhatsApp: Mom", "<b>Did</b> you see the photos?")])

    def test_items(self):
        slack = linux.to_item("Slack", "Priya Raman", "hi", 1.0)
        self.assertEqual((slack["app"], slack["url"]), ("com.tinyspeck.slackmacgap", "slack://open"))
        phone = linux.to_item("KDE Connect", "WhatsApp: Mom", "<b>Did</b> you", 1.0)
        self.assertEqual((phone["app_name"], phone["body"]), ("KDE Connect", "Did you"))


class WebhookTest(unittest.TestCase):
    def setUp(self):
        from pile import server
        self.s = store.Store(":memory:")
        self.tok = webhook.token(self.s)
        handler = server.make_handler(self.s, threading.Lock(), {})
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, path, body, headers):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_ingest_needs_token(self):
        payload = {"app": "Deploys", "title": "prod is green", "url": "https://ci.example.com/42"}
        code, _ = self.post("/ingest", payload, {"Content-Type": "application/json"})
        self.assertEqual(code, 401)
        self.assertEqual(self.s.items(), [])
        code, _ = self.post("/ingest", payload, {"Content-Type": "application/json", "Authorization": "Bearer nope"})
        self.assertEqual(code, 401)
        code, r = self.post("/ingest", payload, {"Content-Type": "application/json", "Authorization": "Bearer " + self.tok})
        self.assertEqual(code, 200)
        self.assertEqual(self.s.get(r["id"])["link"], "https://ci.example.com/42")

    def test_ingest_rejects_script_urls(self):
        with self.assertRaises(ValueError):
            webhook.ingest(self.s, {"app": "x", "title": "y", "url": "javascript:alert(1)"})
        with self.assertRaises(ValueError):
            webhook.ingest(self.s, {"app": "x", "title": "y", "url": "file:///Applications/Calculator.app"})
        self.assertTrue(webhook.ingest(self.s, {"app": "x", "title": "y", "url": "slack://open"})["id"])

    def test_ui_api_refuses_foreign_hosts_and_form_posts(self):
        code, _ = self.post("/api/done", {"ids": [1]}, {"Content-Type": "text/plain"})
        self.assertEqual(code, 403)
        code, _ = self.post("/api/done", {"ids": [1]}, {"Content-Type": "application/json", "Host": "evil.example.com"})
        self.assertEqual(code, 403)
        code, _ = self.post("/api/done", {"ids": []}, {"Content-Type": "application/json"})
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()
