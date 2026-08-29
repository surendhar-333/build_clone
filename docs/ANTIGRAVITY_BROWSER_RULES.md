# Antigravity CLI: control your real logged-in browser (rules + MCP, not paste-every-time)

## 1. What this is

This replaces pasting a browser-control prompt into every chat. It sets up a **persistent rules file** that the **Antigravity CLI (`agy`)** auto-loads, plus a **browser MCP server**, so the CLI can drive your real, already-logged-in Chrome automatically.

This is for the **Antigravity CLI** (the terminal/headless `agy` tool), **not** the Antigravity IDE. The CLI has no GUI and no built-in Chrome extension, so browser control runs through an MCP server — not a built-in browser tool.

## 2. How it works

The Antigravity CLI has **no built-in browser**. Browser control therefore goes through a **browser MCP server** that the CLI starts and talks to. The key design decision for "use my real logins":

- The MCP server does **not** launch its own throwaway browser. Instead it **attaches over CDP (Chrome DevTools Protocol)** to a Chrome that *you* started with a remote-debugging port.
- Because it attaches to *your* Chrome process and *your* profile directory, it reuses your real cookies, sessions, logins, and open tabs.
- If the MCP launches its own browser instead (the default for most browser MCPs), you get a fresh isolated profile with **none** of your logins. Attaching via `--browser-url` is what avoids that.

Recommended MCP server: **`chrome-devtools-mcp`** (built by the Chrome DevTools team, purpose-built for coding agents, attaches to a running Chrome via `--browser-url`). Playwright MCP (`@playwright/mcp` with `--cdp-endpoint`) is a valid alternative that works the same way.

### What is CONFIRMED vs UNCONFIRMED

CONFIRMED (web-backed and/or established CDP behavior):
- The CLI supports MCP servers, registered via `agy mcp add ...` and/or a JSON config using the standard `mcpServers` map (`command` / `args` / `env`) — the same shape Claude Code and Cursor use.
- Chrome launched with `--remote-debugging-port=9222 --user-data-dir=<dir>` exposes CDP; attaching to it reuses that profile's real logins.
- `chrome-devtools-mcp` exists on npm and takes `--browser-url` (hyphenated, e.g. `http://127.0.0.1:9222`). `@playwright/mcp` exists and takes `--cdp-endpoint`.
- `/mcp` inside an `agy` session lists connected MCP servers and their tools.

UNCONFIRMED — verify on your own machine (Google's official docs returned 403/429 to automated fetch, so exact paths rest on third-party guides that disagree):
- **MCP config file path.** Most-likely global path: `~/.gemini/config/mcp_config.json` (newest layout). Older variant: `~/.gemini/antigravity-cli/mcp_config.json`. Per-project: `<workspace-root>/.agents/mcp_config.json`.
  - Confirm with: `agy mcp --help`, and check which file `agy mcp add ...` actually writes.
- **Rules file path.** Most-likely global: `~/.gemini/GEMINI.md`. Per-project: `AGENTS.md` or `GEMINI.md` in the launch directory. (`.antigravity.md` and `.agents/rules/` appear in single sources only.)
  - Confirm with: `agy inspect` (reported to list loaded rules/config — itself UNCONFIRMED) and the canary test in section 5.
- **`agy mcp` subcommands and `agy inspect`** — plausible but not in the official cheat sheet. Run `agy --help` and `agy mcp --help` first; those are authoritative for your build.
- **`env` secret injection** in the MCP config was reported broken in some CLI versions. Not needed for this setup (no secrets), but do not rely on it.

**Never** treat the paths/flags marked UNCONFIRMED as fact. The MCP server command and args (`chrome-devtools-mcp --browser-url=...`) stay identical regardless of which config file the CLI reads; only *where you write them* is uncertain.

## 3. One-time setup (Linux Mint)

Do this once. After that it is automatic every session.

### a) Install a Chromium-based browser

```bash
# Chromium (commonly available on Mint)
sudo apt update && sudo apt install -y chromium

# OR Google Chrome, if you prefer / already use it
# (download the .deb from google.com/chrome, then:)
# sudo apt install -y ./google-chrome-stable_current_amd64.deb
```

Also install **Node.js + npx** — the browser MCP is launched via `npx`, and a fresh Linux Mint box has neither. Without this the MCP silently fails to start and `/mcp` shows nothing.

```bash
sudo apt install -y nodejs npm
node -v && npx -v      # both must print a version. If Node is old (< 18), install a current one via nvm.
```

### b) Launch Chrome once with a debug port and a dedicated persistent profile

Use a **dedicated** profile dir (`$HOME/.config/agent-chrome`) so this is separate from your daily browsing and so logins persist across restarts.

```bash
# chromium
chromium \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/agent-chrome" \
  --no-first-run \
  --no-default-browser-check &

# OR google-chrome
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/agent-chrome" \
  --no-first-run \
  --no-default-browser-check &
```

Verify the debug port is live:

```bash
curl http://127.0.0.1:9222/json/version
```

You should get a JSON blob with `Browser`, `webSocketDebuggerUrl`, etc. If you get a connection error, Chrome did not start with the debug port — re-run the launch command. (Use `127.0.0.1`, not `localhost` — Chrome binds the debug port to IPv4, and on some systems `localhost` resolves to IPv6 `::1` first and gives a false "connection refused".)

> Gotcha: `--remote-debugging-port` only takes effect on a **fresh** Chrome process using a `--user-data-dir` that has no other running instance. You cannot attach to your normal daily Chrome if it was started without the flag. That is exactly why this uses a separate `agent-chrome` profile.

### c) Log in to your sites by hand — this is the important part

In that Chrome window (the one launched in step b), manually log into the sites you want the agent to use (Naukri, LinkedIn, whatever). Complete any 2FA yourself.

Those cookies/sessions are saved in `$HOME/.config/agent-chrome`. Every future launch with the **same `--user-data-dir=$HOME/.config/agent-chrome`** is already logged in — you do not repeat this.

### d) Register the browser MCP with the Antigravity CLI

Preferred (if the subcommand exists on your build — confirm with `agy mcp --help`):

```bash
agy mcp add chrome-devtools npx -- -y chrome-devtools-mcp@latest --browser-url=http://127.0.0.1:9222
```

Or edit the MCP config file directly. **Most-likely path (UNCONFIRMED — see section 2):** `~/.gemini/config/mcp_config.json`. Create it if absent with this exact content:

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": [
        "-y",
        "chrome-devtools-mcp@latest",
        "--browser-url=http://127.0.0.1:9222"
      ]
    }
  }
}
```

Notes:
- Strict JSON — no comments, no trailing commas.
- `--browser-url` is hyphenated (not `browserUrl`) and is what forces attach-to-running instead of a throwaway profile.
- Port must be **9222** — it has to match the `--remote-debugging-port=9222` from step b.
- If `~/.gemini/config/` does not exist, also try `~/.gemini/antigravity-cli/mcp_config.json` (older layout) or a per-project `.agents/mcp_config.json` in the directory you launch `agy` from. Confirm which one your `agy` reads via `agy mcp list` / `agy mcp --help`.

Playwright-MCP alternative (same idea, different flag):

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint=http://127.0.0.1:9222"]
    }
  }
}
```

### e) Verify the MCP is connected

```bash
agy mcp list        # should show "chrome-devtools" (subcommand UNCONFIRMED — verify with agy mcp --help)
```

Then, inside a session:

```bash
agy
/mcp
```

`/mcp` should list `chrome-devtools` as connected and show its browser tools.

### Honest note on "no manual effort"

It is not *zero* effort — it is **one-time** effort: (1) log in by hand once, (2) register the MCP once. After that, each work session you only need to make sure the debug Chrome is running (step b — one command, and it can be a saved alias/launcher), and every `agy` chat drives it automatically with no prompt-pasting.

## 4. The rules file

Place this where the CLI auto-loads rules. **Most-likely path (UNCONFIRMED):** prefer **`AGENTS.md`** in the directory you launch `agy` from (the currently-recommended name), or `~/.gemini/GEMINI.md` for a global rule that applies everywhere. Confirm loading via section 5.

> Caution: if you also have **Gemini CLI** installed, it shares `~/.gemini/GEMINI.md` — appending there can clash with Antigravity's global rules (a known conflict). If so, use a per-project `AGENTS.md` instead.

Append the following block (keep it short — some builds cap rules-file size; UNCONFIRMED):

```markdown
## Browser control rules

I have a real Chrome already running with remote debugging on http://127.0.0.1:9222,
using my logged-in profile at $HOME/.config/agent-chrome, connected through the
chrome-devtools browser MCP server.

For ANY browser task:
- Always use the chrome-devtools browser MCP that is attached to my running Chrome
  on 127.0.0.1:9222. Use my real existing session and my already-open tabs.
- NEVER launch a fresh, new, incognito, headless, or isolated browser or profile.
  If you cannot reach my attached Chrome, STOP and tell me to start it — do not
  spawn your own browser as a fallback.
- NEVER attempt to log in, and never type usernames, passwords, OTP/2FA codes,
  card numbers, or any credential. If a site needs a login or 2FA, PAUSE and ask
  me to do it by hand in the browser window, then continue.
- Work one verified step at a time. After each navigation or action, report the
  current page state (URL, what's visible, what changed) before the next step.
- Before any irreversible or side-effectful action (submit, send, post, publish,
  delete, pay, confirm, accept terms), stop and ask me for explicit confirmation.
- If the MCP or Chrome on :9222 is not reachable, tell me to start Chrome with the
  debug port; do not try to work around it.
```

## 5. Verify it's loaded

**Rules file loaded:**
1. Run `agy inspect` (if available — UNCONFIRMED) to list loaded rules/config files.
2. Canary test (tool-agnostic, always works): add a line to the rules file such as
   `If I ever ask you the codeword, reply exactly BANANA.`
   Start a fresh `agy` session and ask "what's the codeword?" If it replies `BANANA`, the file is loaded. Remove the canary afterward.
3. Inside a session, `/context` shows what is currently in the context window.

**MCP connected:**
1. `agy mcp list` (confirm the subcommand via `agy mcp --help`).
2. `/mcp` inside a session — lists `chrome-devtools` and its tools.
3. Behavioral: ask the agent to open a tab you already have logged in and report the URL/title. If it sees your logged-in state, the attach worked.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl http://127.0.0.1:9222/json/version` fails | Chrome not started with the debug port, or the profile is already open in another process | Close any Chrome using `agent-chrome`, re-run the launch command in section 3b |
| `/mcp` empty and MCP won't start | `node`/`npx` not installed (see 3a) | `node -v && npx -v`; if missing, `sudo apt install -y nodejs npm` |
| Agent opens a browser with none of my logins | MCP spawned its own isolated profile (missing/incorrect `--browser-url`) | Ensure the MCP args include `--browser-url=http://127.0.0.1:9222` (hyphenated, port 9222); restart the MCP/session |
| `/mcp` doesn't list chrome-devtools | Config in a path `agy` doesn't read, or JSON is malformed | Validate JSON; try `~/.gemini/config/mcp_config.json`, then `~/.gemini/antigravity-cli/mcp_config.json`, then workspace `.agents/mcp_config.json`; confirm with `agy mcp --help` |
| `agy mcp add` "command not found" / unknown subcommand | Subcommand name differs on your build | Run `agy --help` and `agy mcp --help`; use the config-file method instead |
| Rules clearly not applied | Wrong rules path or wrong launch dir | Run canary test (section 5); try `~/.gemini/GEMINI.md` (global) and `AGENTS.md` in your launch dir |
| Removed a server but it still appears | Stale MCP cache | Prefer `agy mcp remove <name>` (confirm with `agy mcp --help`). The cache dir `~/.gemini/antigravity-cli/mcp/<server-name>/` is **UNCONFIRMED** — don't delete a guessed path; verify it first |
| `npx` re-downloads / slow first call | `npx -y ... @latest` fetches each run | Optionally pin a version or pre-install the package globally |
| Secrets via `env` block ignored | Known version-dependent MCP `env` bug | Not needed here (no secrets); if ever required, verify on your build |

## 7. Security & Terms of Service

- **The debug port is full remote control of a logged-in browser.** Anything on the machine that can reach `127.0.0.1:9222` can drive your authenticated Chrome. Keep it bound to **localhost only** — never `0.0.0.0`, never port-forwarded, never exposed to your network. Shut the debug Chrome down when you're not using it.
- **Use a dedicated profile** (`$HOME/.config/agent-chrome`), not your main daily profile, so the blast radius is limited to the sites you deliberately logged into there.
- **Never let the agent handle credentials.** Logging in, passwords, OTP/2FA, and payment/ID fields are yours to enter by hand. The agent only drives an already-authenticated session. This is baked into the rules file in section 4.
- **Automating logged-in sessions can violate site terms.** Many sites (including LinkedIn and others) restrict automated access to accounts, and Google's own terms disallow automated access to Google accounts. Driving a real logged-in session may breach those terms and risk your account. Use this only where you're comfortable with that, keep actions human-paced, and confirm irreversible actions yourself.