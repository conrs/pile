"""What agents see: secrets dropped, people turned into stable pseudonyms, context kept.

Deterministic and local; no model needed. The approach follows Viva la Trace
(github.com/MT-GoCode/viva-la-trace): drop secrets, drop PII, keep context, and give every
redacted thing a content-hashed name so it can still be referred to.

    r = Redactor(store)
    r.text("Your code is 482913, call Dana at 415-555-0100", names=["Dana"])
    -> "Your code is [code], call Person-7f3a at Phone-91c2"

Pseudonyms are an HMAC of the real value with a local key (redact.key next to the database),
so they're stable across items and runs, meaningless elsewhere, and mapped back only here.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets as _secrets
from typing import Iterable, Optional

SCHEMA = "CREATE TABLE IF NOT EXISTS pseudonyms (token TEXT PRIMARY KEY, kind TEXT, real TEXT)"

OTP = re.compile(r"(?i)\b(code|otp|passcode|verification|verify|pin|one[- ]time)\b([^0-9\n]{0,30}?)(\d[\d -]{2,8}\d)\b")
OTP_BEFORE = re.compile(r"\b(\d{4,8})\b(?=[^.\n]{0,25}\b(?:is your|is the|verification|one[- ]time|security code|login code)\b)", re.I)
KEYS = re.compile(r"\b(sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                  r"xox[abposr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
                  r"(?:pile|bc|t2t)_pat_[A-Za-z0-9_-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")
LONG = re.compile(r"(?<![A-Za-z0-9/])(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9/])")
PASSWORD = re.compile(r"(?i)\b(password|passwd|pwd|passphrase)(\s*(?:is|:|=)\s*)(\S+)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{6,}\d(?![\w])")
ADDRESS = re.compile(r"\b\d{1,5}\s+(?:[A-Z][a-z]+\.?\s){1,3}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|"
                     r"Way|Ct|Court|Pl|Place|Ter|Terrace|Pkwy|Parkway)\b\.?(?:,?\s*(?:Apt|Unit|Suite|#)\s*\w+)?")


def luhn(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total, alt = total + n, not alt
    return total % 10 == 0


def is_phone(s: str) -> bool:
    n = len(re.sub(r"\D", "", s))
    return 10 <= n <= 11 or (s.strip().startswith("+") and 10 <= n <= 15)


def iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def key_path(store) -> Optional[str]:
    if store is None or store.path == ":memory:":
        return None
    return os.path.join(os.path.dirname(os.path.abspath(store.path)), "redact.key")


def load_key(store) -> bytes:
    path = key_path(store)
    if not path:
        return _secrets.token_bytes(32)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        key = _secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key


def enabled() -> bool:
    return os.environ.get("PILE_REDACT", "").lower() in ("1", "true", "yes", "on")


class Redactor:
    def __init__(self, store, allow: Iterable[str] = ()):
        self.store = store
        self.key = load_key(store)
        if store is not None:
            store.db.execute(SCHEMA)
        allowed = os.environ.get("PILE_REDACT_ALLOW", "").split(",") + [os.environ.get("PILE_MY_NAME", "")]
        self.allow = {a.strip().lower() for a in list(allow) + allowed if a.strip()}

    # ---- pseudonyms ----
    def pseudonym(self, real: str, kind: str = "Person") -> str:
        norm = re.sub(r"\s+", " ", real.strip().lower())
        token = "%s-%s" % (kind, hmac.new(self.key, (kind + ":" + norm).encode(), hashlib.sha256).hexdigest()[:4])
        if self.store is not None:
            self.store.db.execute("INSERT OR IGNORE INTO pseudonyms(token, kind, real) VALUES(?,?,?)", (token, kind, real.strip()))
        return token

    def unmap(self, token: str) -> Optional[str]:
        if self.store is None:
            return None
        row = self.store.db.execute("SELECT real FROM pseudonyms WHERE token=?", (token,)).fetchone()
        return row[0] if row else None

    def unmap_text(self, s: str) -> str:
        return re.sub(r"\b(?:Person|Email|Phone|Thread)-[0-9a-f]{4}\b", lambda m: self.unmap(m.group(0)) or m.group(0), s or "")

    def allowed(self, name: str) -> bool:
        n = (name or "").strip().lower()
        return not n or n in self.allow

    # ---- text ----
    def text(self, s: str, names: Iterable[str] = ()) -> str:
        if not s:
            return s or ""
        s = KEYS.sub("[secret]", s)
        s = PASSWORD.sub(lambda m: m.group(1) + m.group(2) + "[secret]", s)
        s = OTP.sub(lambda m: m.group(1) + m.group(2) + "[code]", s)
        s = OTP_BEFORE.sub("[code]", s)
        s = CARD.sub(lambda m: "[card]" if luhn(re.sub(r"\D", "", m.group(0))) else m.group(0), s)
        s = IBAN.sub(lambda m: "[iban]" if iban_ok(m.group(0)) else m.group(0), s)
        s = EMAIL.sub(lambda m: m.group(0) if self.allowed(m.group(0)) else self.pseudonym(m.group(0), "Email"), s)
        s = PHONE.sub(lambda m: self.pseudonym(re.sub(r"\D", "", m.group(0))[-10:], "Phone")
                      if is_phone(m.group(0)) else m.group(0), s)
        s = ADDRESS.sub("[address]", s)
        s = LONG.sub("[secret]", s)
        for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
            if self.allowed(name) or len(name) < 2:
                continue
            token = self.pseudonym(name)
            s = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", token, s, flags=re.I)
            first = name.split()[0]
            if len(first) >= 3 and first != name and not self.allowed(first):
                s = re.sub(r"(?<!\w)" + re.escape(first) + r"(?!\w)", token, s, flags=re.I)
        return s

    # ---- items ----
    def is_person(self, item: dict) -> bool:
        sender = (item.get("sender") or "").strip()
        if not sender or self.allowed(sender) or sender == item.get("app_name"):
            return False
        return item.get("app") != "github" and "/" not in sender  # repos are public names

    def people(self) -> list:
        """Full names of everyone who has written to you, so a mention in someone else's message is caught too.
        Only names of two or more words: a one-word sender ("Calendar", "Mom") isn't reliable enough to hunt for."""
        if getattr(self, "_people", None) is None:
            rows = self.store.db.execute("SELECT DISTINCT sender, app, app_name FROM items") if self.store else []
            self._people = [r[0] for r in rows if r[0] and len(r[0].split()) >= 2
                            and self.is_person({"sender": r[0], "app": r[1], "app_name": r[2]})]
        return self._people

    def item(self, item: dict) -> dict:
        out = dict(item)
        names = ([item.get("sender") or ""] if self.is_person(item) else []) + self.people()
        if self.is_person(item):
            out["sender"] = self.pseudonym(item["sender"])
        for k in ("title", "body"):
            out[k] = self.text(item.get(k) or "", names)
        for k in ("link", "url"):
            if k in out:
                out[k] = self.text(out.get(k) or "", names)
        if os.environ.get("PILE_REDACT_MODEL", "").lower() in ("1", "true", "yes", "on") and out.get("body"):
            out["body"] = self.reframe(out["body"])
        if "thread" in out:
            out["thread"] = self.pseudonym(item["thread"], "Thread")
        out.pop("raw_thread", None)
        return out

    # ---- optional model pass (off unless PILE_REDACT_MODEL=1), after the deterministic one ----
    _cache = {}

    def reframe(self, text: str) -> str:
        """A neutral one-line summary from the configured triage endpoint. Falls back to the redacted text."""
        if text in self._cache:
            return self._cache[text]
        from . import triage
        cfg = triage.config()
        if not cfg:
            return text
        import json
        import urllib.request
        base, model, key = cfg
        prompt = ("Rewrite this message as one neutral sentence describing what it asks or says. Keep pseudonyms like "
                  "Person-7f3a and placeholders like [code] exactly; add no names, numbers or details that aren't there.")
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps({
            "model": model, "temperature": 0,
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}]}).encode(),
            headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + key} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()
            out = self.text(out)  # never trust the model to have kept things out
        except Exception:
            out = text
        self._cache[text] = out
        return out
