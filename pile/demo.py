"""Realistic fake items for trying pile out (and for screenshots). Nobody here is real."""
from __future__ import annotations

import time

from . import deeplinks

M = 60
H = 3600

# (minutes ago, app, sender, title, body, url or None, label)
ITEMS = [
    (2, "com.tinyspeck.slackmacgap", "Priya Raman", "#launch", "Can you sanity-check the pricing page before 9? Copy is in the doc", None, "needs-you"),
    (4, "com.apple.MobileSMS", "Sam", "", "running 10 late, grab us a table?", None, "needs-you"),
    (6, "github", "acme/web", "Fix race in session refresh (#412)", "PullRequest · review requested", "https://github.com/acme/web/pull/412", "needs-you"),
    (9, "com.tinyspeck.slackmacgap", "Priya Raman", "#launch", "Also the hero image is still the old one", None, "needs-you"),
    (11, "com.hnc.Discord", "Jordan", "#playtesters", "new build crashes on level 3 when you pause during the boss", None, "needs-you"),
    (14, "com.linear", "Linear", "ENG-218 assigned to you", "Import fails on CSVs with a BOM", None, "needs-you"),
    (18, "gmail", "Dana Whitfield", "Re: Thursday intro", "Great, Thursday 2pm works. I'll send a calendar invite shortly.", "https://mail.google.com/mail/u/0/#search/rfc822msgid:demo-1%40mail.example.com", "fyi"),
    (23, "github", "acme/web", "CI failed on main", "CheckSuite · ci activity", "https://github.com/acme/web/actions", "fyi"),
    (27, "com.tinyspeck.slackmacgap", "Alex Chen", "", "lunch tomorrow? the ramen place reopened", None, "fyi"),
    (35, "com.hnc.Discord", "Mika", "#general", "anyone else seeing the dark mode flicker?", None, "fyi"),
    (41, "com.apple.MobileSMS", "Mom", "", "Did you see the photos from Sunday?", None, "fyi"),
    (52, "github", "acme/api", "Rate limiter drops bursts after deploy (#97)", "Issue · mention", "https://github.com/acme/api/issues/97#issuecomment-1", "needs-you"),
    (64, "com.linear", "Linear", "ENG-201 moved to Done", "Retry uploads on 503", None, "fyi"),
    (80, "gmail", "Stripe", "Your receipt from Cloud Hosting Co", "Amount paid $24.00", "https://mail.google.com/mail/u/0/#search/rfc822msgid:demo-2%40mail.example.com", "noise"),
    (95, "_WEB_CENTER_:web.calendar.example.com", "Calendar", "Design review in 15 minutes", "Room 4B · video link in invite", None, "fyi"),
    (130, "gmail", "The Weekly Byte", "Issue #212: the browser is the OS now", "This week: local-first apps, sqlite everywhere, and more", "https://mail.google.com/mail/u/0/#search/rfc822msgid:demo-3%40mail.example.com", "noise"),
    (180, "com.hnc.Discord", "Server Boosts", "", "Your server reached Level 2!", None, "noise"),
    (240, "github", "acme/docs", "Bump lodash from 4.17.20 to 4.17.21", "PullRequest · subscribed", "https://github.com/acme/docs/pull/33", "noise"),
]


def seed(store, now=None) -> int:
    now = now or time.time()
    n = 0
    for i, (ago, app, sender, title, body, url, label) in enumerate(ITEMS):
        source = {"github": "github", "gmail": "imap"}.get(app, "macos")
        key = title if title.startswith("#") or sender == deeplinks.app_name(app) else sender
        thread = "github|demo%d" % i if app == "github" else app + "|" + key
        if store.upsert({
            "source": source, "ext_id": "demo-%d" % i, "app": app, "app_name": deeplinks.app_name(app),
            "thread": thread, "sender": sender, "title": title, "body": body, "at": now - ago * M,
            "url": url or deeplinks.default_link(app), "label": label, "label_by": "demo",
        }):
            n += 1
    return n
