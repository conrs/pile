"""Optional labelling by any OpenAI-compatible endpoint (OpenRouter, Ollama, ...).

Off unless PILE_BASE_URL (or BASE_URL) and PILE_MODEL (or MODEL) are set.
Your learned label rules always run first; the model only sees what they didn't cover.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from . import rules

PROMPT = """You triage a person's notifications. Label each item:
- needs-you: a person is waiting on them, something is broken, or it has a deadline
- fyi: worth seeing, no action needed
- noise: automated, promotional or social filler
Reply with JSON only: {"labels": [{"id": <id>, "label": "needs-you|fyi|noise"}]}"""


def config():
    base = os.environ.get("PILE_BASE_URL") or os.environ.get("BASE_URL")
    model = os.environ.get("PILE_MODEL") or os.environ.get("MODEL")
    key = os.environ.get("PILE_API_KEY") or os.environ.get("API_KEY") or ""
    return (base.rstrip("/"), model, key) if base and model else None


def ask(items: list, base: str, model: str, key: str) -> dict:
    lines = [{"id": i["id"], "app": i["app_name"], "from": i["sender"], "title": i["title"],
              "body": i["body"][:200]} for i in items]
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps({"model": model, "temperature": 0, "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(lines, ensure_ascii=False)}]}).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + key} if key else {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = json.loads(r.read().decode())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    out = {}
    for row in (json.loads(m.group(0)).get("labels", []) if m else []):
        if row.get("label") in rules.LABELS:
            out[int(row["id"])] = row["label"]
    return out


def run(store, batch: int = 30) -> dict:
    todo = [i for i in store.items("new") if not i["label"]]
    by_rule = 0
    for item in todo:
        lab = rules.label_for(store, item)
        if lab:
            store.set_label(item["id"], lab, "rule")
            by_rule += 1
    todo = [i for i in todo if not rules.label_for(store, i)]
    cfg = config()
    by_model = 0
    if cfg and todo:
        for start in range(0, len(todo), batch):
            for item_id, lab in ask(todo[start:start + batch], *cfg).items():
                store.set_label(item_id, lab, "model")
                by_model += 1
    return {"by_rule": by_rule, "by_model": by_model, "model": bool(cfg), "left": len(todo) - by_model}
