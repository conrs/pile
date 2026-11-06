"""Is this email a task? Rules only, no model: a real person, written to you, asking for something."""
from __future__ import annotations

import re

BULK_SENDER = re.compile(r"(no-?reply|do-?not-?reply|notifications?@|notify@|mailer-daemon|bounce|newsletter|"
                         r"marketing@|news@|updates?@|info@|hello@|support@|receipts?@|billing@)", re.I)
ASK = re.compile(r"\b(can|could|would|will) you\b|\bplease\b|\?|\bby (mon|tues|wednes|thurs|fri|satur|sun)day\b|"
                 r"\bby (tomorrow|tonight|eod|end of (the )?(day|week))\b|\bdeadline\b|\breview\b|\bsign\b|\bapprove\b|"
                 r"\blet me know\b|\bneed you\b|\basap\b|\bwhen can\b|\bare you (free|around|available)\b", re.I)


def is_bulk_sender(address: str) -> bool:
    return bool(BULK_SENDER.search(address or ""))


def asks(text: str) -> bool:
    return bool(ASK.search(text or ""))


def is_task(person: bool, direct: bool, text: str, answered: bool) -> bool:
    """person: not a list or robot. direct: you're in To, not just Cc. answered: you replied."""
    return person and direct and not answered and asks(text)
