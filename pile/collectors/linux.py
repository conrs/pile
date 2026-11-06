"""Linux desktop notifications, live, via `dbus-monitor` on the session bus.

Catches anything that pops a notification: Slack, Discord, Signal, Thunderbird, and
Android phone notifications mirrored by KDE Connect or GSConnect.
Runs while `pile watch` or `pile serve` is running.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time

from .. import deeplinks

NAME = "linux"
PLATFORMS = ("linux",)
NEEDS = "dbus-monitor (in the dbus package) and a desktop session"
ORDER = 12
MATCH = "interface='org.freedesktop.Notifications',member='Notify'"

_STR = re.compile(r'^   string "(.*)$')
_BY_NAME = {v[0].lower(): k for k, v in deeplinks.APPS.items()}


def check(**_):
    import os
    if not shutil.which("dbus-monitor"):
        return False, "install dbus-monitor (Debian/Ubuntu: dbus-bin; Fedora/Arch: dbus)"
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False, "no session bus: run pile from your desktop session"
    return True, "listening while `pile watch` or `pile serve` runs"


def parse(lines):
    """dbus-monitor output -> Notify calls as (app_name, summary, body). Pure, for tests."""
    vals, cur, in_call = [], None, False
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("method call") or line.startswith("signal") or line.startswith("method return"):
            if in_call and len(vals) >= 5:
                yield vals[0], vals[3], vals[4]
            in_call, vals, cur = "member=Notify" in line, [], None
            continue
        if not in_call or len(vals) >= 5:
            continue
        if cur is not None:  # a string spanning lines
            if line.endswith('"'):
                vals.append(cur + "\n" + line[:-1]); cur = None
            else:
                cur += "\n" + line
            continue
        m = _STR.match(line)
        if m:
            s = m.group(1)
            if s.endswith('"') and not s.endswith('\\"'):
                vals.append(s[:-1])
            else:
                cur = s
        elif line.startswith("   uint32 "):
            vals.append(line.split()[-1])
        elif line.startswith("   "):
            vals.append("")
    if in_call and len(vals) >= 5:
        yield vals[0], vals[3], vals[4]


def to_item(app_name: str, summary: str, body: str, at: float) -> dict:
    key = _BY_NAME.get(app_name.lower(), "linux:" + app_name)
    body = re.sub(r"<[^>]+>", "", body)  # notification bodies may carry simple markup
    return {"source": NAME, "ext_id": hashlib.sha1(("%s|%s|%s|%.3f" % (app_name, summary, body, at)).encode()).hexdigest(),
            "app": key, "app_name": deeplinks.app_name(key) if not key.startswith("linux:") else app_name or "Desktop",
            "thread": key + "|" + summary, "sender": summary, "title": "", "body": body, "at": at,
            "url": deeplinks.default_link(key) if not key.startswith("linux:") else ""}


def listen(store, stop, lock=None, **_):
    proc = subprocess.Popen(["dbus-monitor", "--session", MATCH], stdout=subprocess.PIPE, text=True, bufsize=1)
    try:
        def lines():
            for line in proc.stdout:
                if stop.is_set():
                    return
                yield line
        for app, summary, body in parse(lines()):
            if lock:
                with lock:
                    store.upsert(to_item(app, summary, body, time.time()))
            else:
                store.upsert(to_item(app, summary, body, time.time()))
    finally:
        proc.terminate()
