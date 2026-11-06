"""Fixtures: a fake Notification Center database and fake GitHub notifications."""
import plistlib
import sqlite3

MAC_EPOCH = 978307200


def make_nc_db(path, records):
    """records: (bundle, title, subtitle, body, unix_time[, thread id]). Mirrors usernoted's db2 tables."""
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier VARCHAR, badge INTEGER NULL);
      CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER, uuid BLOB, data BLOB,
                           request_date REAL, request_last_date REAL, delivered_date REAL,
                           presented Bool, style INTEGER, snooze_fire_date REAL);
    """)
    apps = {}
    for i, rec in enumerate(records):
        bundle, title, subt, body, at = rec[:5]
        if bundle not in apps:
            apps[bundle] = len(apps) + 1
            db.execute("INSERT INTO app(app_id, identifier) VALUES(?,?)", (apps[bundle], bundle))
        req = {"titl": title, "body": body}
        if subt:
            req["subt"] = subt
        if len(rec) > 5:
            req["thre"] = rec[5]
        data = plistlib.dumps({"app": bundle, "date": at - MAC_EPOCH, "uuid": bytes([i] * 16), "req": req},
                              fmt=plistlib.FMT_BINARY)
        db.execute("INSERT INTO record(app_id, data, delivered_date) VALUES(?,?,?)",
                   (apps[bundle], data, at - MAC_EPOCH))
    db.commit()
    db.close()


def gh(nid, kind, api_url, title, repo="acme/web", reason="review_requested", updated="2026-11-05T18:00:00Z",
       comment=None):
    return {"id": str(nid), "unread": True, "reason": reason, "updated_at": updated,
            "subject": {"title": title, "url": api_url, "type": kind, "latest_comment_url": comment},
            "repository": {"full_name": repo, "html_url": "https://github.com/" + repo}}


def make_chat_db(path, chats, now):
    """chats: (guid, display_name, [handles], [(from_handle or None for me, text, minutes_ago, attributed?)])."""
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
      CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT, style INTEGER);
      CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
      CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER,
                            is_from_me INTEGER, date INTEGER, associated_message_type INTEGER DEFAULT 0);
      CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER);
    """)
    hid = {}
    for ci, (guid, name, handles, msgs) in enumerate(chats, 1):
        for h in handles:
            if h not in hid:
                hid[h] = len(hid) + 1
                db.execute("INSERT INTO handle VALUES (?,?)", (hid[h], h))
            db.execute("INSERT INTO chat_handle_join VALUES (?,?)", (ci, hid[h]))
        db.execute("INSERT INTO chat VALUES (?,?,?,?,?)", (ci, guid, handles[0], name, 43 if len(handles) > 1 else 45))
        for mi, m in enumerate(msgs):
            who, text, ago = m[:3]
            attributed = None
            if len(m) > 3 and m[3]:
                raw = text.encode()
                attributed = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + bytes([len(raw)]) + raw + b"\x86"
                text = None
            ns = int((now - ago * 60 - MAC_EPOCH) * 1e9)
            cur = db.execute("INSERT INTO message(guid, text, attributedBody, handle_id, is_from_me, date) VALUES (?,?,?,?,?,?)",
                             ("%s-m%d" % (guid, mi), text, attributed, hid.get(who), 0 if who else 1, ns))
            db.execute("INSERT INTO chat_message_join VALUES (?,?,?)", (ci, cur.lastrowid, ns))
    db.commit()
    db.close()
