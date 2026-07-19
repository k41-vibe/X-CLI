# Xcli

A command-line client **and** terminal UI for **X (Twitter)**, built on the
site's **unofficial internal GraphQL API** — the same endpoints the x.com web
app calls. No developer account, no official API keys.

> ⚠️ **Use at your own risk.** Automating the private API violates X's Terms of
> Service and can get your account suspended. Intended for personal, low-volume,
> self-use only. Built-in rate limiting is on by default; keep it that way.

---

## Features

**CLI** (scriptable — pipe `--json` into your own tools)

- Post / delete, with **image / video attachments** and **threads**
- Like / retweet / bookmark (+ undo), **quote**, follow / unfollow
- Mute / block (+ undo)
- Home timeline (following & for-you), any user's profile & tweets
- Search (top / latest / people / media / lists, with `from:` / `min_faves:` filters)
- Likes, bookmarks, notifications
- Direct messages: inbox, read, send, delete
- Conversation / reply threads
- Media: show URLs, inline preview, download
- `--json` export on every read command

**TUI** (`xcli tui`) — a mouse-driven terminal interface

- Tabs: Following / For you / Search / Notifications / DM
- Per-tweet buttons: like, retweet, bookmark, reply, quote, thread, media, delete
- Click a name to open the author's profile; follow / mute / block from there
- Inline image previews, quote boxes, infinite scroll, confirm dialogs
- Startup splash logo
- **English or Japanese UI**: `xcli tui --en` (default is Japanese)

## How it works

- Endpoints: `https://x.com/i/api/graphql/{queryId}/{Operation}`
- Auth: public web Bearer token + your `auth_token` / `ct0` cookies +
  `x-csrf-token` + `x-client-transaction-id`
- `x-client-transaction-id` is generated with
  [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction)
- `queryId` / `features` change with X's deploys, so `xcli sync` refreshes them
  from the public JS bundle, and missing `features` are auto-healed from the API's
  own error responses

## Install

```bash
pip install -e .
```

Pulls in `requests`, `beautifulsoup4`, `XClientTransaction`, `Pillow`,
`textual`, and `rich-pixels`.

## Setup

Your credentials live **only** in `~/.xcli/config.json` (outside the repo) — the
code ships with empty defaults. Provide your own cookies once:

1. In Chrome, open x.com → DevTools (F12) → **Application → Cookies → https://x.com**
2. Copy the values of **`auth_token`** and **`ct0`**
3. Paste them into `~/.xcli/config.json`:

```json
{
  "auth_token": "<your auth_token>",
  "ct0": "<your ct0>",
  "lang": "ja",
  "min_interval_sec": 2.0
}
```

`auth_token` is a session credential — treat it like a password, never commit or
share it. It is long-lived, so this is a one-time step until it expires
(`[認証エラー]` / auth error means it's time to refresh).

## Usage

```bash
xcli sync                          # refresh queryIds (first run / when it breaks)
xcli whoami                        # your handle

xcli post "hello"                  # post
xcli post "with pics" --media a.jpg b.jpg
xcli thread "1/2" "2/2"            # thread
xcli quote <id> "nice"            # quote tweet
xcli delete <id>

xcli like <id>   / xcli unlike <id>
xcli rt <id>     / xcli unrt <id>
xcli bookmark <id> / xcli unbookmark <id>
xcli follow  <handle|id> / xcli unfollow <handle|id>
xcli mute    <handle|id> / xcli unmute   <handle|id>
xcli block   <handle|id> / xcli unblock  <handle|id>

xcli timeline -n 50 [--algo]       # following (or for-you)
xcli user <handle> --tweets
xcli search "query" --tab latest   # top/latest/people/media/lists
xcli likes <handle> / xcli bookmarks / xcli notifications
xcli conversation <id>             # reply thread
xcli media <id> [--show|--open|--download DIR]

xcli dms / xcli dm <handle> "text" / xcli dm-read <handle> / xcli dm-delete <msg_id>

xcli timeline --json | jq '.[].text'   # export / pipe into other tools

xcli tui        # terminal UI (Japanese)
xcli tui --en   # terminal UI (English)
```

## License

[MIT](LICENSE) © 2026 k41-vibe

---

🤖 **Co-created with [Claude](https://www.anthropic.com/claude) (Anthropic).**
The unofficial API was reverse-engineered and the CLI + TUI were designed and
implemented together with Claude.
