# pile

Take back your inbox.

You have accounts on a ton of platforms to keep up with. They want to keep you on them, so they make it hard to leave. But we can use their own greed against them: they all send push notifications and emails to pull you back in. pile catches those and builds one inbox from them, so you can skip their crappy UIs and actually get your job done.

[![MIT](https://img.shields.io/badge/license-MIT-ffb547)](LICENSE)
[![stdlib only](https://img.shields.io/badge/dependencies-none%20(Python%20stdlib)-6fd49a)](pyproject.toml)
[![platforms](https://img.shields.io/badge/runs%20on-macOS%20%7C%20Linux%20%7C%20Windows-8fa6c8)](#platforms)

![pile: j/k through the feed, open, done, inbox zero](docs/hero.gif)

Every notification and unread lands in one local feed. Each item links back to the actual thread, PR or email. Any agent can work it over MCP. It runs on your machine, stores one SQLite file, and has no dependencies beyond Python 3.

## 60-second quickstart (Mac)

1. System Settings > Privacy & Security > **Full Disk Access**: add your terminal app (Terminal, iTerm, Ghostty...), then restart it. pile reads macOS's Notification Center database, read-only, and that file is behind this permission.
2. Run:

```sh
git clone https://github.com/conrs/pile && cd pile
python3 -m pile doctor                      # what works here, and how to turn on the rest
python3 -m pile collect && python3 -m pile serve
```

3. Open http://127.0.0.1:8765. `serve` keeps collecting every 30 seconds.

Just looking? `python3 -m pile serve --demo` runs on made-up data in a separate database.

macOS deletes notifications once you dismiss them, so pile can only see what's still in Notification Center (it keeps its own copy from then on). For the older stuff you never followed up on:

```sh
python3 -m pile backfill --days 30
```

That adds Messages conversations where they spoke last and you never replied, open GitHub review requests and assigned issues (plus older notifications), and mail from real people you never answered (lists and no-reply senders skipped). They get an "unanswered" chip and their own Unanswered tab, oldest first.

[Setup video (75 s)](docs/setup.mp4)

## Keys

![pile keys](docs/keys.png)

| | | | |
|---|---|---|---|
| `j` `k` move | `o` / Enter open | `e` done | `s` snooze (hour, tonight, tomorrow, next week) |
| `+` always important | `-` not important | `f` fix where it opens | `c` combine rows into one conversation, `C` split |
| `x` select, `Shift`+`j`/`k` extend | `m` mute this app | `/` filter | `z` undo (again and again), `Shift`+`Z` redo |
| `?` what it's learned | `p` screen-share mode | `0`-`9` filter by source | `r` collect now |

With rows selected (`x`, `Shift`+`j`/`k`, Shift-click, Cmd/Ctrl-click), `e`, `s`, `+`, `-`, `m` and `c` act on all of them at once, as one step that one `z` undoes. Undo is a stack kept in the database, so it goes all the way back, even after a restart. Every action also has a button on the row, so a mouse or a touchscreen works too.

Items from the same conversation group into one row (pile uses the thread id apps attach to their notifications, so a group text is one row); select it to see the earlier messages, and `e` clears the whole row. If several rows are really one conversation, select them and press `c`: they fold into the newest one, and pile remembers it.

**Screen-share mode** (`p`, or open `/?private`) blurs every name, message and avatar so you can demo or stream your real inbox. `/?private&keep=github` leaves repo names readable.

**History:** `pile history` lists what you did (open, done, snooze, labels, link fixes), newest first, `--json` for scripts. `pile reopen --since 2h` (or `--all`, `--source github`) puts cleared items back. `pile list --state done` shows what you cleared. `pile undo` and `pile redo` work from the terminal too.

**Opening Messages:** a Messages notification only names the sender, so pile looks the message up in `chat.db` (by its text and time) to open that exact conversation, and keeps the link. `pile open-test <id>` shows which link it would use and why.

## Platforms

| Collector | Runs on | What it reads | Needs |
|---|---|---|---|
| `macos` | macOS | Notification Center database (every app and website that notifies you) | Full Disk Access for your terminal |
| `messages` | macOS | `chat.db`: conversations waiting on your reply (`pile backfill`) | Full Disk Access for your terminal |
| `windows` | Windows | `%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db` | nothing (it's your own file); install Python from python.org |
| `linux` | Linux | desktop notifications, live, via `dbus-monitor` | a desktop session; runs while `watch` or `serve` runs |
| `github` | any | GitHub notifications | `GITHUB_TOKEN`, or the `gh` CLI logged in |
| `imap` | any | unread mail (Gmail with an app password, or any IMAP server) | `PILE_IMAP_USER`, `PILE_IMAP_PASSWORD`, optional `PILE_IMAP_HOST` |
| `webhook` | any | anything you POST to `/ingest` | `pile token` |

`python3 -m pile doctor` tells you which of these work on your machine and what to do about the rest.

**Android.** No app needed yet. Either mirror your phone's notifications to your computer with [KDE Connect](https://kdeconnect.kde.org/) (Linux, Windows) or GSConnect (GNOME), and the desktop collector picks them up; or push them with Tasker, MacroDroid or ntfy to the webhook:

```sh
python3 -m pile serve --listen 0.0.0.0       # the UI stays local-only; /ingest needs the token
curl -X POST http://<your-computer>:8765/ingest \
  -H "Authorization: Bearer $(python3 -m pile token)" -H "Content-Type: application/json" \
  -d '{"app": "WhatsApp", "sender": "Sam", "body": "running late", "url": "whatsapp://"}'
```

A native Android companion app is future work.

## How it fits together

```mermaid
flowchart LR
  subgraph your computer
    NC[macOS Notification Center] --> C
    WN[Windows notifications] --> C
    DB[Linux D-Bus notifications] --> C
    GH[GitHub API] --> C
    IM[IMAP mail] --> C
    WH[POST /ingest<br>phone, scripts] --> C
    C[collectors] --> S[(one SQLite file)]
    R[learned rules] --- S
    S --> UI[web UI<br>127.0.0.1:8765]
    S --> M[MCP server<br>stdio]
  end
  M --> A[Claude, or any agent]
  UI -- o --> APP[the app, thread or page]
```

## Agents (MCP)

`python3 -m pile mcp` speaks MCP over stdio. Hand-rolled JSON-RPC, no SDK.

Claude Code:

```sh
claude mcp add pile -- python3 -m pile mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pile": { "command": "python3", "args": ["-m", "pile", "mcp"], "cwd": "/path/to/pile" }
  }
}
```

Then ask "which three of these matter?" or "snooze everything from GitHub until tomorrow".

Tools: `list_inbox {source?, state?, label?, tag?, limit?}`, `history {limit?}`, `undo {steps?}`, `redo {steps?}`, `get_item {id}`, `mark_done {ids}`, `snooze {ids, until}`, `open_item {id}`, and for teaching it: `fix_link {id, url}`, `set_rule {kind, match, value}`, `list_rules`, `delete_rule {id}`.

## It learns from you

![fixing a deep link](docs/learning.gif)

- **Links.** After you open something, pile asks whether it landed in the right place. Say no and paste where you ended up. If that URL contains the item's sender, title or id (as-is, URL-encoded or slugified), pile turns those into placeholders and the fix covers the whole app: paste `https://acme.slack.com/archives/launch` for `#launch` and every Slack channel item opens its own channel. Otherwise it keeps the fix to that person or thread. Learned links win over the built-in table and show a "learned link" mark.
- **Importance.** `+` means always important from this sender, `-` means not important. These rules label new items before any model sees them, so they work with no model configured.
- **Mute.** `m` skips an app from now on and clears what's there.

Everything learned is in the Learned panel (`?`), where you can delete any of it. Agents can read and write the same rules over MCP.

## Optional: model triage

Off unless you configure it. Point it at any OpenAI-compatible endpoint (OpenRouter, Ollama, LM Studio...):

```sh
export PILE_BASE_URL=http://localhost:11434/v1 PILE_MODEL=llama3.1   # Ollama
# or PILE_BASE_URL=https://openrouter.ai/api/v1 PILE_MODEL=... PILE_API_KEY=...
python3 -m pile triage
```

It labels new items `needs-you`, `fyi` or `noise`; labels show as chips. Your own rules run first.

## Privacy

Everything stays on your machine: one SQLite file at `~/.local/share/pile/inbox.db` (or `--db PATH`). The web UI binds to 127.0.0.1 and refuses other host names and cross-site requests. Nothing leaves your computer unless you turn on model triage, which sends item text to the endpoint you chose. The collectors only read; pile never marks anything read in the source apps.

## Write a collector

A collector is one Python file. Drop it in `~/.config/pile/collectors/` (or `pile/collectors/` to send a PR) and `doctor` finds it:

```python
NAME = "rss"
PLATFORMS = ("any",)                    # or ("darwin",), ("linux", "windows")...
NEEDS = "PILE_RSS_URL"

def check(**opts):
    import os
    return (True, "ok") if os.environ.get("PILE_RSS_URL") else (False, "set PILE_RSS_URL")

def collect(store, **opts):
    n = 0
    for entry in fetch_entries():       # your code
        if store.upsert({
            "source": NAME, "ext_id": entry.id, "app": "rss", "app_name": "RSS",
            "thread": "rss|" + entry.feed, "sender": entry.feed, "title": entry.title,
            "body": entry.summary, "at": entry.timestamp, "url": entry.link,
        }):
            n += 1
    return n
```

Push-style sources (a socket, a D-Bus stream) define `listen(store, stop, **opts)` instead of `collect`. Sources with history can also define `backfill(store, days, **opts)` for `pile backfill`. To teach pile a new app's deep link, add a line to `APPS` in `pile/deeplinks.py`.

## Tests

```sh
python3 -m unittest
```

No network: the tests build a fake Notification Center database (binary plists and all), a fake Windows notification database, captured `dbus-monitor` output and GitHub JSON.

`docs/media/record.sh` re-records the GIFs and the setup video on Linux (Xvfb, Firefox, xdotool, ffmpeg).

## License

MIT
