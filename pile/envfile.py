"""Settings from env files: ~/.config/pile/env, then ./.env. Real environment variables always win."""
from __future__ import annotations

import os

LOADED = []  # files read, for `pile doctor`


def paths() -> list:
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return [os.path.join(cfg, "pile", "env"), os.path.join(os.getcwd(), ".env")]


def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            out[key] = value
    return out


def load(files=None) -> list:
    """Fill in unset variables; an earlier file wins over a later one. Returns the files read."""
    read = []
    for path in files or paths():
        try:
            with open(path, encoding="utf-8") as f:
                values = parse(f.read())
        except OSError:
            continue
        for k, v in values.items():
            os.environ.setdefault(k, v)
        read.append(path)
    LOADED[:] = read
    return read
