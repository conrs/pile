import os
import tempfile
import unittest
from unittest import mock

from pile import envfile, mailtasks
from pile.collectors import imap


class EnvFileTest(unittest.TestCase):
    def test_parse_and_precedence(self):
        d = tempfile.mkdtemp()
        a, b = os.path.join(d, "env"), os.path.join(d, ".env")
        with open(a, "w") as f:
            f.write("# my settings\nPILE_IMAP_USER=you@example.com\nexport PILE_IMAP_PASSWORD='abcd efgh ijkl mnop'\n"
                    "PILE_X=from-config # note\n")
        with open(b, "w") as f:
            f.write('PILE_X="from-dotenv"\nPILE_Y=y\nnot a setting\n')
        env = {"PILE_Y": "real"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(envfile.load([a, b, os.path.join(d, "missing")]), [a, b])
            self.assertEqual(os.environ["PILE_IMAP_PASSWORD"], "abcd efgh ijkl mnop")
            self.assertEqual(os.environ["PILE_X"], "from-config")  # first file wins
            self.assertEqual(os.environ["PILE_Y"], "real")         # real env always wins
            self.assertEqual(os.environ["PILE_IMAP_USER"], "you@example.com")


def mail(frm, to, subject, body, extra=""):
    return ("From: %s\r\nTo: %s\r\nSubject: %s\r\nDate: Thu, 05 Nov 2026 18:00:00 -0800\r\n"
            "Message-ID: <x%d@example.com>\r\n%sContent-Type: text/plain\r\n\r\n%s\r\n"
            % (frm, to, subject, abs(hash(subject)), extra, body)).encode()


class MailTaskTest(unittest.TestCase):
    ME = {"me@example.com"}

    def test_task_needs_a_person_writing_to_you_and_asking(self):
        ask = mail("Dana <dana@example.com>", "me@example.com", "Contract", "Could you sign this by Friday?")
        item = imap.to_item(ask, "imap.gmail.com", {"thrid": 1786123456789, "answered": False}, self.ME)
        self.assertEqual(item["tag"], "task")
        self.assertEqual(item["url"], "https://mail.google.com/mail/u/0/#all/%x" % 1786123456789)
        self.assertEqual(item["thread"], "gmail|thr:%x" % 1786123456789)
        cc = mail("Dana <dana@example.com>", "boss@example.com", "Contract", "Could you sign this?")
        self.assertIsNone(imap.to_item(cc, "imap.gmail.com", {}, self.ME)["tag"])  # only Cc'd/elsewhere: not yours
        fyi = mail("Dana <dana@example.com>", "me@example.com", "Notes", "Here are the notes from today.")
        self.assertIsNone(imap.to_item(fyi, "imap.gmail.com", {}, self.ME)["tag"])
        replied = imap.to_item(ask, "imap.gmail.com", {"answered": True}, self.ME)
        self.assertIsNone(replied["tag"])

    def test_robots_go_to_sift(self):
        news = mail("The Weekly <news@example.com>", "me@example.com", "Issue 12", "Can you believe it?",
                    "List-Unsubscribe: <mailto:u@example.com>\r\n")
        self.assertEqual(imap.to_item(news, "imap.gmail.com", {}, self.ME)["tag"], "sift")
        robot = mail("Shop <no-reply@shop.example.com>", "me@example.com", "Your receipt", "Thanks")
        self.assertEqual(imap.to_item(robot, "imap.gmail.com", {}, self.ME)["tag"], "sift")

    def test_fetch_meta(self):
        m = imap.fetch_meta(b'7 (X-GM-THRID 1786123456789 FLAGS (\\Seen \\Answered) BODY[] {123}')
        self.assertEqual(m, {"thrid": 1786123456789, "answered": True})
        self.assertEqual(imap.fetch_meta(b"7 (FLAGS () BODY[] {1}"), {"thrid": None, "answered": False})

    def test_asks(self):
        self.assertTrue(mailtasks.asks("are you free thursday"))
        self.assertTrue(mailtasks.asks("Please review the draft"))
        self.assertFalse(mailtasks.asks("Thanks, all done here."))


class MailAppTest(unittest.TestCase):
    def make(self, path, now):
        import sqlite3
        db = sqlite3.connect(path)
        db.executescript("""
          CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
          CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject TEXT);
          CREATE TABLE summaries (ROWID INTEGER PRIMARY KEY, summary TEXT);
          CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
          CREATE TABLE message_global_data (ROWID INTEGER PRIMARY KEY, message_id_header TEXT);
          CREATE TABLE recipients (ROWID INTEGER PRIMARY KEY, message INTEGER, address INTEGER, type INTEGER, position INTEGER);
          CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, sender INTEGER, subject INTEGER, summary INTEGER,
            global_message_id INTEGER, date_received INTEGER, mailbox INTEGER, read INTEGER, flags INTEGER,
            deleted INTEGER, list_id_hash INTEGER, unsubscribe_type INTEGER);
          INSERT INTO mailboxes VALUES (1, 'imap://you@imap.example.com/INBOX'), (2, 'imap://you@imap.example.com/Archive');
          INSERT INTO addresses VALUES (1, 'dana@example.com', 'Dana'), (2, 'news@weekly.example.com', 'The Weekly'),
                                       (3, 'me@example.com', ''), (4, 'pat@example.com', 'Pat');
        """)
        msgs = [  # sender, subject, summary, mailbox, read, flags, list_id, to
            (1, "Contract", "Could you sign this by Friday?", 1, 0, 0, 0, 3),
            (2, "Issue 12", "This week in tools", 1, 0, 0, 99, 3),
            (4, "Lunch", "Are you free Thursday?", 1, 0, 4, 0, 3),     # answered
            (1, "Old", "Can you look?", 2, 0, 0, 0, 3),                # not the inbox
            (4, "Seen", "Could you check?", 1, 1, 0, 0, 3),            # read
        ]
        for i, (snd, subj, summ, mb, read, flags, lid, to) in enumerate(msgs, 1):
            db.execute("INSERT INTO subjects VALUES (?,?)", (i, subj))
            db.execute("INSERT INTO summaries VALUES (?,?)", (i, summ))
            db.execute("INSERT INTO message_global_data VALUES (?,?)", (i, "<m%d@example.com>" % i))
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,0,?,0)",
                       (i, snd, i, i, i, int(now) - 3600 * i, mb, read, flags, lid))
            db.execute("INSERT INTO recipients(message, address, type, position) VALUES (?,?,0,0)", (i, to))
        db.commit()
        db.close()

    def test_unread_inbox_mail_with_tasks_and_sift(self):
        import time
        from pile import store
        from pile.collectors import mailapp
        path = os.path.join(tempfile.mkdtemp(), "Envelope Index")
        self.make(path, time.time())
        self.assertTrue(mailapp.check(mail_db=path)[0])
        s = store.Store(":memory:")
        self.assertEqual(mailapp.collect(s, mail_db=path), 3)
        items = {i["title"]: i for i in s.items()}
        self.assertEqual({k: v["tag"] for k, v in items.items()}, {"Contract": "task", "Issue 12": "sift", "Lunch": None})
        self.assertEqual(items["Contract"]["link"], "message://%3Cm1@example.com%3E")
        self.assertEqual(items["Contract"]["sender"], "Dana")


class AccountsTest(unittest.TestCase):
    def test_numbered_and_plain(self):
        env = {"PILE_IMAP_USER": "me@gmail.com", "PILE_IMAP_PASSWORD": "x", "PILE_IMAP_NAME": "personal",
               "PILE_IMAP_2_USER": "me@work.example.com", "PILE_IMAP_2_PASSWORD": "y",
               "PILE_IMAP_2_HOST": "imap.work.example.com", "PILE_IMAP_2_NAME": "work",
               "PILE_IMAP_3_USER": "half@example.com"}  # no password: skipped
        accts = imap.accounts(env)
        self.assertEqual([(a["key"], a["name"], a["host"]) for a in accts],
                         [("1", "personal", "imap.gmail.com"), ("2", "work", "imap.work.example.com")])

    def test_items_carry_their_account(self):
        acct = {"key": "2", "user": "me@gmail.com", "password": "x", "host": "imap.gmail.com", "folder": "INBOX",
                "name": "work"}
        raw = mail("Dana <dana@example.com>", "me@gmail.com", "Hi", "Could you call me?")
        with mock.patch.dict(os.environ, {"PILE_IMAP_USER": "a@gmail.com", "PILE_IMAP_PASSWORD": "x",
                                          "PILE_IMAP_2_USER": "me@gmail.com", "PILE_IMAP_2_PASSWORD": "y",
                                          "PILE_IMAP_2_NAME": "work"}, clear=True):
            item = imap.to_item(raw, "imap.gmail.com", {"thrid": 255}, acct=acct)
        self.assertEqual(item["source"], "imap:work")
        self.assertEqual(item["app_name"], "Gmail · work")
        self.assertEqual(item["url"], "https://mail.google.com/mail/u/?authuser=me@gmail.com#all/ff")
        self.assertEqual(item["tag"], "task")
