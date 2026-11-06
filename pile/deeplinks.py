"""Where "open" goes for each app. One table; add a line to teach it a new app.

Learned rules (see rules.py) beat this table. Anything not listed falls back to
"bundle:<id>", which opens the app itself with `open -b <id>`.
"""
from __future__ import annotations

import platform
import subprocess
import webbrowser

# bundle id -> (display name, deep link)
APPS = {
    "com.tinyspeck.slackmacgap": ("Slack", "slack://open"),
    "com.hnc.Discord": ("Discord", "discord://"),
    "com.apple.MobileSMS": ("Messages", "sms:"),
    "com.apple.mail": ("Mail", "message://"),
    "com.apple.iCal": ("Calendar", "ical://"),
    "com.apple.reminders": ("Reminders", "x-apple-reminderkit://"),
    "com.apple.FaceTime": ("FaceTime", "facetime://"),
    "com.linear": ("Linear", "linear://"),
    "com.microsoft.teams2": ("Teams", "msteams://"),
    "us.zoom.xos": ("Zoom", "zoommtg://"),
    "net.whatsapp.WhatsApp": ("WhatsApp", "whatsapp://"),
    "ru.keepcoder.Telegram": ("Telegram", "tg://"),
    "com.figma.Desktop": ("Figma", "figma://"),
    "notion.id": ("Notion", "notion://"),
    "com.spotify.client": ("Spotify", "spotify://"),
    "github": ("GitHub", "https://github.com/notifications"),
    "gmail": ("Gmail", "https://mail.google.com/mail/u/0/#inbox"),
    "imap": ("Mail", "message://"),
}

WEB_PREFIX = "_WEB_CENTER_:web."


def app_name(bundle: str) -> str:
    if bundle in APPS:
        return APPS[bundle][0]
    if bundle.startswith(WEB_PREFIX):
        return bundle[len(WEB_PREFIX):]
    tail = bundle.rsplit(".", 1)[-1]
    return tail[:1].upper() + tail[1:] if tail else bundle


def default_link(bundle: str) -> str:
    """Built-in deep link for an app: the table, a website push, else the app itself."""
    if bundle in APPS:
        return APPS[bundle][1]
    if bundle.startswith(WEB_PREFIX):
        return "https://" + bundle[len(WEB_PREFIX):]
    return "bundle:" + bundle


def open_link(link: str) -> str:
    """Open a deep link on this machine. Returns what it did."""
    if not link:
        return "nothing to open"
    mac = platform.system() == "Darwin"
    if link.startswith("bundle:"):
        if mac:
            subprocess.run(["open", "-b", link[len("bundle:"):]], check=False)
            return "opened app " + link[len("bundle:"):]
        return "would open app " + link[len("bundle:"):] + " (macOS only)"
    if mac:
        subprocess.run(["open", link], check=False)
        return "opened " + link
    if link.startswith(("http://", "https://")):
        webbrowser.open(link)
        return "opened " + link
    return "would open " + link + " (macOS only)"
