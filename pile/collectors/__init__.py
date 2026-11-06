"""Collectors are plugins: one .py file each, found automatically.

Built-ins live here; your own go in ~/.config/pile/collectors/ (or $PILE_COLLECTORS).
A collector module defines:

    NAME = "mysource"                 # unique; becomes item["source"]
    PLATFORMS = ("darwin", "linux", "windows")   # or ("any",)
    NEEDS = "what it needs, in a few words"

    def check(**opts) -> (bool, str):  # ready?  and "ok" / "missing X: how to enable"
    def collect(store, **opts) -> int: # poll once; store.upsert(item) per thing; return how many

or, for sources that push (like a D-Bus stream), instead of collect:

    def listen(store, stop, **opts):   # block until stop (a threading.Event) is set

opts carries the command-line paths (macos_db, windows_db). Set PATH_OPT to the one your
collector reads, and passing it makes the collector run on any OS (for tests and fixtures).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
import platform
import sys
import threading

HERE = os.path.dirname(__file__)


def this_os() -> str:
    s = platform.system().lower()
    return {"darwin": "darwin", "windows": "windows"}.get(s, "linux" if s == "linux" else s)


def user_dir() -> str:
    return os.environ.get("PILE_COLLECTORS") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "pile", "collectors")


def discover() -> list:
    mods = []
    for info in pkgutil.iter_modules([HERE]):
        mods.append(importlib.import_module(__name__ + "." + info.name))
    d = user_dir()
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py") and not fn.startswith("_"):
                try:
                    spec = importlib.util.spec_from_file_location("pile_user_collector_" + fn[:-3], os.path.join(d, fn))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    mods.append(mod)
                except Exception as e:
                    print("pile: can't load collector %s: %s" % (fn, e), file=sys.stderr)
    mods = [m for m in mods if hasattr(m, "NAME") and hasattr(m, "check")]
    return sorted(mods, key=lambda m: (getattr(m, "ORDER", 50), m.NAME))


def supported(mod, os_name=None) -> bool:
    plats = getattr(mod, "PLATFORMS", ("any",))
    return "any" in plats or (os_name or this_os()) in plats


def status(opts=None) -> list:
    """For `pile doctor`: every collector, whether it works here, and how to enable it."""
    opts = opts or {}
    out = []
    for m in discover():
        forced = getattr(m, "PATH_OPT", None) and opts.get(m.PATH_OPT)  # a fixture path works on any OS
        if not supported(m) and not forced:
            out.append((m, None, "not for %s (runs on %s)" % (this_os(), ", ".join(m.PLATFORMS))))
            continue
        try:
            ok, msg = m.check(**opts)
        except Exception as e:
            ok, msg = False, "check failed: %s" % e
        out.append((m, ok, msg))
    return out


def ready(opts=None) -> list:
    return [m for m, ok, _ in status(opts) if ok]


def run_all(store, quiet=False, **opts) -> dict:
    counts = {}
    for mod in ready(opts):
        if not hasattr(mod, "collect"):
            continue
        try:
            counts[mod.NAME] = mod.collect(store, **opts)
        except Exception as e:  # one broken source never stops the others
            counts[mod.NAME] = "error: %s" % e
            if not quiet:
                print("pile: %s: %s" % (mod.NAME, e), file=sys.stderr)
    return counts


def start_listeners(store, lock, stop, **opts) -> list:
    """Run push-style collectors (listen()) in background threads."""
    threads = []
    for mod in ready(opts):
        if hasattr(mod, "listen"):
            t = threading.Thread(target=mod.listen, args=(store, stop), kwargs=dict(opts, lock=lock), daemon=True)
            t.start()
            threads.append(t)
    return threads
