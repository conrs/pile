"""Collectors. Each is a module with NAME, available() and collect(store) -> count.

To add one: write collectors/<name>.py with those three things, then list it in ALL.
"""
from __future__ import annotations

import sys

from . import github, imap, macos

ALL = [macos, github, imap]


def run_all(store, macos_db=None, quiet=False) -> dict:
    counts = {}
    for mod in ALL:
        try:
            if mod is macos:
                if not macos.available(macos_db):
                    continue
                counts[mod.NAME] = macos.collect(store, macos_db)
            elif mod.available():
                counts[mod.NAME] = mod.collect(store)
        except Exception as e:  # one broken source never stops the others
            counts[mod.NAME] = "error: %s" % e
            if not quiet:
                print("pile: %s: %s" % (mod.NAME, e), file=sys.stderr)
    return counts
