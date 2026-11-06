"""Fixtures: a fake Notification Center database and fake GitHub notifications."""
import plistlib
import sqlite3

MAC_EPOCH = 978307200


def make_nc_db(path, records):
    """records: (bundle, title, subtitle, body, unix_time). Mirrors usernoted's db2 tables."""
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier VARCHAR, badge INTEGER NULL);
      CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER, uuid BLOB, data BLOB,
                           request_date REAL, request_last_date REAL, delivered_date REAL,
                           presented Bool, style INTEGER, snooze_fire_date REAL);
    """)
    apps = {}
    for i, (bundle, title, subt, body, at) in enumerate(records):
        if bundle not in apps:
            apps[bundle] = len(apps) + 1
            db.execute("INSERT INTO app(app_id, identifier) VALUES(?,?)", (apps[bundle], bundle))
        req = {"titl": title, "body": body}
        if subt:
            req["subt"] = subt
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
