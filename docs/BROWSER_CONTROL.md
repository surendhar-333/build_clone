# Driving your real, logged-in browser from an AI agent

> How to make an AI agent operate your actual, already-logged-in Chromium browser on Linux Mint — so every site "just works" — instead of a sterile automated browser that Google blocks.

> **Using the Antigravity CLI (`agy`)?** Don't paste a prompt each time — see
> [ANTIGRAVITY_BROWSER_RULES.md](ANTIGRAVITY_BROWSER_RULES.md) for the rules-file + browser-MCP setup
> that makes this automatic. This document is the underlying general reference (how/why CDP attach works).

---

## 1. Short answer: is Playwright better?

**For your actual goal — yes, but not for the reason you'd expect, and not on its own.** Switching frameworks changes nothing by itself: Playwright launching its *own* browser hits Google's exact same wall you're hitting now. What fixes the block is a change of *architecture*, not framework — you stop letting a driver launch a sterile, automated browser, and instead **attach the agent to a real Chrome that you launched yourself, already logged into Google.** Playwright is the better tool only because it has the mode that does this cleanly (`connectOverCDP`); Selenium/Marionette structurally cannot.

Two hard consequences fall out of that:

- **Chromium beats Firefox for this specific goal.** The "attach to my real, already-logged-in browser" trick runs on the Chrome DevTools Protocol (CDP), and that is a Chromium-family capability. Firefox has deprecated its legacy CDP support in favour of WebDriver BiDi, so there is no clean CDP-attach path into a running system Firefox; and geckodriver/Marionette spin up a fresh, driver-owned profile by default — the "island browser" with no history or logins that you're trying to escape. Keep Firefox as your daily browser if you like, but the *automatable* browser should be Chromium or Google Chrome.
- **The real fix is "log in by hand once, then attach" — never automate the login form itself.** That is where Google applies its harshest bot scoring, and it's why the extension/CDP-attach model "just works" on logged-in sites: it reuses a session that's already authenticated instead of trying to type your password past the gate.

### Launch modes compared

| Launch mode | `navigator.webdriver` | Reuses your real logins? | Google login works? | Setup effort |
|---|---|---|---|---|
| **Selenium + geckodriver/Marionette → Firefox** *(your current setup)* | `true` | No by default — fresh driver-owned profile ("island") | **Blocked** | Low |
| **Playwright launches its own Chromium** | `true` by default | No — fresh, cookie-less profile | **Blocked** | Low |
| **Playwright `connectOverCDP` → Chrome you launched with `--remote-debugging-port`** | `false` | **Yes** — attaches to your real, logged-in profile | **Works** (already authenticated) | Medium (one launch flag + ~15 lines) |
| **In-browser extension (the "Claude in Chrome" / MCP model)** | `false` | **Yes** — it *is* your live session | **Works** | Medium (install + wire up, no port/flags) |

The bottom two rows are the only ones that meet your goal. The `connectOverCDP` attach is the right answer when you want your own scripts to drive the real session; the in-browser extension is the closest match to "just like the browser control in Claude Code." The rest of this document covers both in full detail.

---

## 2. Why your current Firefox/Marionette setup gets blocked

Google's login gate isn't reading your password attempts — it's scoring *how* the browser was started. When a driver (geckodriver/Marionette, or chromedriver) launches a browser for automation, the browser sets a JavaScript property called **`navigator.webdriver` to `true`** — the W3C spec literally requires it. Any web page, including Google's login page, can read that property in one line, and it's the single most reliable "this is a bot" tell.

Marionette also launches Firefox with **automation switches** (the equivalent of Chrome's `--enable-automation`) that flip that flag on and announce the browser as controlled. On top of that, a driver-launched browser starts from a **fresh, empty profile** — no history, no cookies, no prior "this device is trusted" signal — so you're forced onto the actual credential form, which is exactly where Google's scoring is strictest.

Put those together and you get *"this browser or app may not be secure."* The decisive factors are just two: **`navigator.webdriver`**, and **whether you're forced to type credentials at all** versus arriving with a live session cookie. A browser *you* start by hand (no driver, no automation switch) never flips the flag and already holds your session — so there's no login form to fail.

That is the whole insight behind the rest of this document: launch the browser yourself, log in yourself once, and have the agent *attach* to the session that already exists.

---

## 3. One-time setup on Linux Mint — exact steps

This is a **one-time** ritual. You do it once, log in once, and from then on every agent/automation session attaches to this same browser and inherits all your real logins. Follow the steps in order.

### 3.1 Install a Chromium-family browser

You need **Chromium** *or* **Google Chrome** (either works — the automation flags are identical). Pick one.

Linux Mint blocks Ubuntu's Snap redirection and ships Chromium as a genuine native `.deb`, so `apt` gives you a clean, non-sandboxed browser — exactly what you want for this (a Snap/Flatpak browser cannot cleanly open an external `--user-data-dir` or expose its debug port reliably).

**Option A — Chromium via apt (native .deb, simplest on Mint):**

```bash
sudo apt update
sudo apt install chromium
```

Verify it installed and note the binary path:

```bash
which chromium
chromium --version
```

**Option B — Google Chrome via the official .deb (choose this if you specifically want Chrome + Google sign-in):**

```bash
cd /tmp
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb
```

Use `apt install ./file.deb` (not `dpkg -i`) so dependencies resolve automatically. This also registers Google's apt repo + signing key, so `apt upgrade` keeps Chrome current. Verify:

```bash
which google-chrome
google-chrome --version
```

> For the rest of this document, `chrome` means "whichever binary you installed" — either `chromium` or `google-chrome`. The flags are identical.

### 3.2 Create a dedicated debug profile directory

Do **not** use your normal browser profile for this. Create a separate `--user-data-dir` that exists only for agent-driven automation:

```bash
mkdir -p "$HOME/.config/agent-chrome"
```

Three reasons this dedicated directory is mandatory, not optional:

1. **Chrome 136+ hard requirement.** Since Chrome 136 (early 2025), `--remote-debugging-port` is **silently ignored when it targets the default profile directory** — an anti-cookie-theft hardening. A non-default `--user-data-dir` is the sanctioned way to get the debug port to open at all.
2. **Profile lock.** Chrome puts a `SingletonLock` on a profile dir. If your everyday browser is open on the default profile, a second launch against that same dir just hands off to the running process and the debug port never binds. A separate dir lets your daily browser stay open.
3. **Blast radius.** An open debug port is unauthenticated full control of that profile. Isolating it to a dedicated dir limits what's exposed to that one profile's cookies.

### 3.3 Launch the browser with the debug port

Run **one** of the following. The `&` backgrounds it so you keep your terminal.

**Chromium variant:**

```bash
chromium \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/agent-chrome" \
  --no-first-run \
  --no-default-browser-check &
```

**Google Chrome variant:**

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/agent-chrome" \
  --no-first-run \
  --no-default-browser-check &
```

What each flag does and why it's here:

- **`--remote-debugging-port=9222`** — opens the CDP/DevTools control endpoint on `localhost:9222`. This is the port your agent / Playwright `connectOverCDP` attaches to. `9222` is convention; any free port works, but keep it consistent so your reusable prompt always matches.
- **`--user-data-dir="$HOME/.config/agent-chrome"`** — points at the dedicated profile from step 3.2. Required for the port to open (Chrome 136+) and for logins to persist across sessions.
- **`--no-first-run`** — skips the first-run setup wizard.
- **`--no-default-browser-check`** — suppresses the "make me your default browser?" nag.

A fresh-looking browser window opens. The first time, it will look empty and logged out — that's expected. You fix that in step 3.6.

### 3.4 Rule: fully quit any browser already using that dir

The single most common failure is the **profile-lock / hand-off** problem: if a process is already running against `$HOME/.config/agent-chrome`, your launch command won't open the port — it just focuses the existing window.

- Your **normal daily browser** (on the default profile) can stay open — it uses a different dir and won't collide.
- But any **previous debug session** on this dir must be closed first. Kill only that instance (never a blanket `pkill chrome`, which would also close your real browser):

```bash
pkill -f "user-data-dir=$HOME/.config/agent-chrome"
```

If the browser crashed and left a stale lock behind, remove it before relaunching:

```bash
rm -f "$HOME/.config/agent-chrome/SingletonLock"
```

Then re-run the launch command from step 3.3.

### 3.5 Verify the debugging endpoint is live

Before doing anything else, confirm the port actually opened:

```bash
curl http://localhost:9222/json/version
```

A healthy response is JSON like this:

```json
{
  "Browser": "Chrome/127.0.6533.88",
  "Protocol-Version": "1.3",
  "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ...",
  "V8-Version": "12.7.224.16",
  "WebKit-Version": "537.36 (@...)",
  "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/ab12cd34-...-ef56"
}
```

How to read it:

- **You got JSON back** → the port is open and the agent can attach. You're good.
- **`webSocketDebuggerUrl`** is the browser-level CDP WebSocket. You normally do **not** copy this by hand — Playwright's `connect_over_cdp('http://localhost:9222')` reads it from `/json/version` for you. It's shown here only so you can recognize a healthy response.
- **`curl: (7) Connection refused`** → the port never opened. Almost always the profile-lock / hand-off problem from step 3.4 (a browser was already using that dir), or you launched against the *default* profile by mistake. Quit the offending instance, remove any stale `SingletonLock`, and relaunch.

To list the individual open tabs (each with its own `webSocketDebuggerUrl`):

```bash
curl http://localhost:9222/json
```

### 3.6 Log in MANUALLY, once, to your sites — this is the step that makes everything "just work"

**In the browser window you just launched** (the one on the `agent-chrome` profile), sign in **by hand** to every site you want your agent to be able to operate:

1. Go to **Google** — `https://accounts.google.com` — and sign in yourself: type your own password, complete your own 2FA, and tick **"remember this device"** / **"trust this browser"** if offered.
2. Go to **LinkedIn** — `https://www.linkedin.com/login` — sign in by hand.
3. Go to **Naukri** — `https://www.naukri.com/nlogin/login` — sign in by hand.
4. Sign in to anything else you'll want automated, the same way.

Why this specific step is the whole point:

- The **credential-entry form is where Google applies its harshest bot scoring** — that scripted-login page is exactly what produces *"this browser or app may not be secure."* By typing your password yourself, once, in a normally-launched browser (no automation switches, `navigator.webdriver` is `false`), you sail through.
- Those logins write **durable session cookies** into `$HOME/.config/agent-chrome`. They persist across restarts. From now on, every time you relaunch with the step 3.3 command and an agent attaches, it arrives at an **already-authenticated** session — there is no login form left to fail.
- **The rule going forward:** the agent must **never** type your passwords and **never** touch 2FA. It only ever operates a session that is *already* logged in. If a site logs you out, you re-login by hand in this window and the agent resumes. This is the discipline that keeps the "not secure" wall from ever appearing again.

> Tip: don't sign out of these sites in this profile. Signing out is what forces the login form (and its bot scoring) to reappear.

### 3.7 Keep the port bound to localhost only

`--remote-debugging-port=9222` binds to **`127.0.0.1`** (loopback) by default. That is correct and you should leave it that way. **Never** expose the port on `0.0.0.0`, and never forward or tunnel it over the network — the reasoning and full hygiene rules are in Section 8. When you're not actively using agent control, close the debug browser:

```bash
pkill -f "user-data-dir=$HOME/.config/agent-chrome"
```

Reopen it with the step 3.3 command when you next need agent control — your logins will still be there.

At this point setup is done. You have a Chromium-family browser, on a dedicated profile, listening on `localhost:9222`, already logged into Google / LinkedIn / Naukri with real trusted sessions. Every later section attaches to *this* browser — never launches a fresh island.

---

## 4. Connecting an agent to it

You now have a Chromium browser running with a debug port open on `127.0.0.1:9222`, logged into your sites in a dedicated profile. This section is about the other half: getting an *agent* (or your own script) to actually drive that browser. There are two fundamentally different architectures, and the difference matters more than any single line of code — so read both before you pick.

- **Architecture A — CDP-attach.** You launched Chrome yourself (Section 3); a script *attaches* to it over the Chrome DevTools Protocol and drives it. Best when you want your own repeatable Python/Node scripts controlling the real session.
- **Architecture B — In-browser extension / MCP browser server.** An extension lives *inside* your normal browser and exposes control to a chat agent. This is the "Claude in Chrome" model, and it is the closest thing to the "it just works like Claude Code" experience you described.

The recommendation is at the end of the section. Start with the code, because the CDP-attach path is what most people wire up first and it makes the tradeoffs concrete.

### 4.1 Architecture A — Playwright `connectOverCDP`

The whole idea: **you** start Chrome (not Playwright), and Playwright *connects to* the already-running browser instead of launching its own. Because Playwright never launched it, none of the automation launch switches are present, `navigator.webdriver` stays `false`, and — critically — the cookies, history, and logins already sitting in that profile are right there for you to use.

#### Install (no browser download needed)

```bash
# Python
pip install playwright

# Node
npm i playwright
```

Note what you do **not** run: `playwright install`. That command downloads Playwright's own bundled, patched Chromium builds. You do not want those — you are attaching to the real Chrome/Chromium you already installed and logged into. `connectOverCDP` uses *your* browser over the wire, so the bundled-browser download is unnecessary. (If you install the `playwright` package and it nags about missing browsers, ignore it; attach mode does not use them.)

#### The default-context gotcha — this is the whole game

When you attach, Playwright hands you a `Browser` object that exposes the existing contexts. The trap, shown per language (note the syntax differs — Python uses a property and snake_case, Node uses methods and camelCase):

```
# Python (sync/async):   ✅ existing session  →  browser.contexts[0]
#                         ❌ empty island      →  browser.new_context()

// Node:                  ✅ existing session  →  browser.contexts()[0]
//                        ❌ empty island      →  browser.newContext()
```

Creating a new context is the reflex you bring from normal Playwright, and it is **exactly** how you end up back on the "island browser with no history/logins" you are trying to escape. When you attach over CDP, the live session is the *first existing context* — `browser.contexts[0]` in Python, `browser.contexts()[0]` in Node. Never create a new context — grab the existing one. Every subsequent action (open tabs, new tabs) must go *through* that existing context, or it won't carry your session. The runnable code blocks below use the correct form for each language.

#### Python (Playwright, sync API)

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # Attach to the ALREADY-RUNNING Chrome you launched with --remote-debugging-port=9222.
    # Pass the HTTP endpoint; Playwright reads /json/version to find the right ws:// URL itself.
    browser = p.chromium.connect_over_cdp("http://localhost:9222")

    # THE GOTCHA: use the existing default context. Do NOT call new_context().
    context = browser.contexts[0]                 # your real, logged-in session
    print("contexts:", len(browser.contexts))
    print("open tabs:", [pg.url for pg in context.pages])

    # Reuse an already-open tab, or open a new one IN THE SAME context.
    page = context.pages[0] if context.pages else context.new_page()

    # Navigate — you're already authenticated, no login form appears.
    page.goto("https://myaccount.google.com")
    page.wait_for_load_state("domcontentloaded")
    print("title:", page.title())

    # Read something off the page.
    heading = page.query_selector("h1")
    if heading:
        print("heading:", heading.inner_text())

    # Click something (example — role-based locators are the robust choice).
    # page.get_by_role("link", name="Security").click()

    # IMPORTANT: do NOT call browser.close() if you want your browser to stay up.
    # Leaving the `with` block just detaches; your Chrome keeps running.
```

#### Node (Playwright)

```js
const { chromium } = require('playwright');

(async () => {
  // Attach to the already-running Chrome.
  const browser = await chromium.connectOverCDP('http://localhost:9222');

  // THE GOTCHA: existing default context, NOT newContext().
  const context = browser.contexts()[0];          // real, logged-in session
  console.log('contexts:', browser.contexts().length);
  console.log('open tabs:', context.pages().map(p => p.url()));

  // Reuse an open tab or open a new one in the SAME context.
  const page = context.pages().length ? context.pages()[0]
                                      : await context.newPage();

  await page.goto('https://myaccount.google.com');  // already logged in
  await page.waitForLoadState('domcontentloaded');
  console.log('title:', await page.title());

  const heading = await page.$('h1');
  if (heading) console.log('heading:', await heading.innerText());

  // Click example:
  // await page.getByRole('link', { name: 'Security' }).click();

  // Detach WITHOUT quitting your browser. In connectOverCDP mode, browser.close()
  // only DISCONNECTS Playwright — it does NOT close your Chrome. (In launch mode it would.)
  await browser.close();
})();
```

#### Endpoint form: use `http://`, not hand-written `ws://`

`connect_over_cdp` / `connectOverCDP` accept either the HTTP root (`http://localhost:9222`) or a raw DevTools WebSocket URL (`ws://localhost:9222/devtools/browser/<uuid>`). **Prefer the HTTP form.** Playwright fetches `/json/version` and reads the correct `webSocketDebuggerUrl` for you, which is version-proof. A hand-written `ws://localhost:9222` guesses the socket path and can point at the wrong endpoint across Chrome versions. Only reach for the explicit `ws://` URL if you have a specific reason to.

#### The one line that kills your browser by accident

Know which mode you are in:

- **Launch mode** (`chromium.launch(...)` / `launchPersistentContext(...)`) — `browser.close()` **quits the browser**.
- **Attach mode** (`connectOverCDP`) — `browser.close()` **only disconnects**; your Chrome stays open.

Since you are always in attach mode here, `browser.close()` is safe and simply detaches. But do not port a `close()` habit from launch-mode scripts without remembering the distinction.

#### What Architecture A gives you, honestly

- Reuses your real logins/cookies/history — **yes**, as long as you use `contexts()[0]`.
- Avoids the obvious `navigator.webdriver` / automation-switch tells — **yes**, because you launched the browser by hand.
- Setup cost — **low**: one launch command plus ~15 lines.
- "Just works like Claude Code" feel — **not quite**. You (or an agent) still run code against a port. It is scripting the real session, not chatting at it.

### 4.2 Architecture B — the in-browser extension / MCP browser server

This is the "Claude in Chrome" model, and it is architecturally different from everything above. Instead of a debug port and an external script attaching in, a **browser extension runs inside your normal browser** and exposes control to an agent — typically by bridging out (via Native Messaging) to an MCP server that the agent talks to. The extension operates your actual tabs, so it inherits whatever you are logged into, natively.

There is no separate context, no debug port, no `--user-data-dir` ritual, no launch flag. The extension *is* your session.

| Property | Architecture B (extension / MCP) |
|---|---|
| Reuses existing logins/cookies/history? | **Yes, natively** — there is no separate context to get wrong. It operates your real tabs. |
| Avoids automation bot-flags? | **Yes — the stealthiest option.** No WebDriver, no CDP debug port, no automation launch switch. To a site it is an ordinary user in an ordinary browser that happens to have an extension installed. |
| Setup cost | **Medium.** Install the extension, grant permissions, and (for MCP servers) wire the native-messaging host + MCP config. But: no launch flags, no port to manage, no lock conflicts. |
| "Just works like Claude Code" feel | **This *is* that experience.** A reusable prompt + an extension that already sees your logged-in tabs is exactly the "not some island browser" outcome you asked for. |

One honest caveat that applies to *both* architectures: the extension route uses `chrome.debugger` under the hood and will raise a "started debugging this browser" infobar — but it does **not** set `navigator.webdriver=true`, which is the flag Google's login gate keys on. So the stealth advantage holds.

In this very environment the `mcp__claude-in-chrome__*` and `mcp__Control_Chrome__*` tools are precisely this architecture: an MCP browser server bridging an agent into a Chromium browser you are already using. That is the concrete shape of "the reusable setup you paste into any chat."

#### When to prefer B over A

- You want **chat-driven, ad-hoc control** ("go check my order status", "fill this in from the doc") rather than a script you'll rerun.
- You want the **minimum bot-detection surface** — no open debug port, no CDP client a hardened anti-bot wall could fingerprint.
- You want **zero per-run launch ritual** — no remembering to start Chrome with the flag, no profile-lock gotchas.
- You are fine installing and trusting an extension in your everyday browser.

#### When to prefer A over B

- You want **your own repeatable scripts** (cron jobs, pipelines, tests) driving the real session deterministically.
- You want **fine-grained programmatic control** — precise selectors, waits, network interception, response reading — that a chat agent doesn't expose as cleanly.
- You're comfortable with the launch-with-a-flag ritual and managing the debug port.

### 4.3 Recommendation

**For "run the browser where all my logins already work, that I can interact with, like the browser control in Claude Code" — the answer is Architecture B (the in-browser extension / MCP browser server).** It reuses your real session with no per-run launch ritual, keeps no debug port sitting open, and does not raise the WebDriver flag. It is, structurally, the thing you described wanting.

**Use Architecture A (`connectOverCDP`) when you want your own scripts to drive the real browser** — repeatable automation you write and rerun, where you value precise programmatic control over the chat-native feel.

Many people run both: the extension for chat-driven, ad-hoc control, and CDP-attach for repeatable scripts. They are not mutually exclusive — both operate the *same* real, logged-in Chromium session, just through different front doors. What neither of them is, and what you should never fall back to for this goal, is a fresh Playwright/Selenium-*launched* browser: that is the island browser with the automation flags flipped on, and it is exactly what put you on Google's "this browser or app may not be secure" wall in the first place.

---

## 5. Prompt A — the browser-control preamble

Paste this at the **start** of any chat where you want the agent to drive your real, logged-in browser. It sets the rules for the whole session. Save it somewhere you can copy in one keystroke (a snippet manager, a shell alias that prints it, or the top of this repo's README).

```text
BROWSER CONTROL MODE — read before doing anything with a browser.

I have a real Chrome/Chromium already running on this machine with a remote-
debugging port open at http://localhost:9222. It uses my dedicated automation
profile (--user-data-dir), and I am ALREADY logged into my sites in it. Your job
is to drive THAT browser, not a new one.

Rules — follow all of them:

1. ATTACH, never launch. Connect to the running browser over CDP at
   http://localhost:9222 (Playwright: chromium.connect_over_cdp("http://localhost:9222");
   or the browser MCP/extension if that's what's wired up). Do NOT launch a fresh
   browser, do NOT use Playwright's bundled Chromium, do NOT start a driver-owned
   Firefox/Chrome.

2. USE MY EXISTING SESSION. Grab the existing default context — browser.contexts()[0]
   — which holds my cookies and logins. NEVER call new_context() and NEVER open an
   incognito/private window. If you find yourself logged out of a site, that means
   you're in the wrong context — stop and tell me, don't try to "fix" it by logging in.

3. NEVER TOUCH CREDENTIALS. Do not type usernames, passwords, OTPs, or 2FA codes.
   Do not click through a Google/SSO login form. If a task requires logging in, PAUSE
   and ask me to do it by hand; then continue once I confirm I'm logged in.

4. ONE STEP AT A TIME. Read the page (DOM/accessibility tree) BEFORE acting. Take a
   single action, then re-read and report what changed. Don't batch a chain of clicks
   blind.

5. CONFIRM BEFORE ANYTHING IRREVERSIBLE. Sending, posting, submitting, paying,
   deleting, or accepting terms — describe exactly what you're about to do and wait
   for my explicit "yes" first.

6. REPORT STATE. After each step tell me: current URL, page title, and what you
   observed or changed. If a site blocks automation or behaves oddly, say so and stop
   rather than retrying blindly.

7. NEVER close my browser. In attach mode, disconnect when done — do not call any
   close that would quit Chrome.

Acknowledge these rules, then wait for my task.
```

---

## 6. Prompt B — the step-by-step operating SOP

Paste this when you're ready to hand the agent an actual task, or reference it once and tell the agent "follow the SOP." It's the procedure the agent executes for **every** browser task.

```text
BROWSER TASK SOP — follow these steps in order for the task I give you.

PREFLIGHT
1. Confirm the debug endpoint is alive before anything else. Run:
      curl -s http://localhost:9222/json/version
   Expect JSON with a "Browser" field and a "webSocketDebuggerUrl". If you get
   "Connection refused" or empty output, STOP and tell me to launch the browser
   (see the launch command); do not try to start it yourself.
2. Attach over CDP to http://localhost:9222 and take the EXISTING default context
   (contexts()[0]). Print: number of contexts, and the URLs of all open tabs/pages.
3. Confirm with me WHICH tab to act on (or which URL to open). Do not assume. If you
   need a new tab, open it in the existing context — never a new context.
4. Read the target page's DOM / accessibility tree and summarize what's actually on
   it BEFORE you touch anything.

ACT–VERIFY LOOP (repeat per step)
5. State the single next action in plain words ("click the 'Compose' button").
6. If that action is destructive/irreversible (send, post, submit, pay, delete,
   accept terms), ask for my explicit confirmation and WAIT.
7. Perform exactly one action.
8. Re-read the page. Report: new URL, title, and whether the expected change
   happened. If it didn't, do NOT retry blindly — describe what you see and ask.
9. Loop to step 5 for the next action.

LOGIN HANDLING
10. If you hit a login form, a "sign in" wall, or a session-expired page: STOP. Do
    not type anything. Tell me exactly what page you're on and ask me to log in by
    hand in that browser window. Wait for my "done", then re-read the page and
    continue.

LOST / RE-DETECTED SESSION
11. If a site that was working suddenly shows logged-out, a CAPTCHA, or a "this
    browser may not be secure"/automation-block page: STOP. Report it verbatim. Do
    not attempt to solve a CAPTCHA and do not re-login. Ask me whether to (a) let me
    handle that site by hand, or (b) skip it.
12. If the CDP connection drops mid-task, re-run the preflight curl (step 1) and
    re-attach. If it won't attach, tell me — the browser may have been closed.

STOP CONDITION
13. Stop when the task's success criteria are met (state them back to me), OR when
    you hit a login/CAPTCHA/block you can't pass, OR when an irreversible step needs
    my confirmation and I haven't given it. Always end with a short summary: what you
    did, the final URL/state, and anything left for me to do by hand.
```

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| **`connectOverCDP` hangs or times out** | Nothing is listening on 9222 — the browser isn't running with the port, or it handed the URL to an already-open instance and never bound the port. | `curl -s http://localhost:9222/json/version` first. If refused, (re)launch Chrome with `--remote-debugging-port=9222` **and** a non-default `--user-data-dir`. Confirm no firewall/localhost proxy is interfering. Pass the **HTTP** endpoint (`http://localhost:9222`), not a hand-written `ws://`. |
| **Got a blank / cookieless context (logged out "island")** | You called `new_context()` (or opened incognito) instead of using the existing default context. | Use `browser.contexts()[0]`. Never `new_context()`. Open new tabs *inside* that context. If `contexts()` is empty, you attached to the wrong browser/profile — check you launched the debug profile you actually logged into. |
| **"Port already in use" on launch** | A previous debug Chrome (or another process) is still holding 9222. | `pkill -f "user-data-dir=$HOME/.config/agent-chrome"` to kill only the debug instance (not your daily browser), then relaunch. Or pick another free port (e.g. `--remote-debugging-port=9223`) and attach to that. |
| **Browser won't start / "profile is already in use" / profile locked** | The `user-data-dir` is locked by a running instance, or a stale `SingletonLock` remains after a crash. | Ensure no instance is using that dir (`pkill -f "user-data-dir=$HOME/.config/agent-chrome"`). Remove the stale lock: `rm -f "$HOME/.config/agent-chrome/SingletonLock"`. Never blanket `pkill chrome` — that closes your real browser too. |
| **Google still says "this browser or app may not be secure"** | You (or the agent) are being pushed through the **credential form** — the debug profile isn't logged in yet, or automation typed into the login page. | Log into Google **by hand, once**, in the debug profile, complete 2FA yourself, let "remember this device" stick. Never let the agent type credentials. Also confirm you *launched* the browser yourself (attach mode) rather than letting a driver launch it — a driver-launched browser sets `navigator.webdriver=true` and re-triggers the block. |
| **A specific site still detects a bot / blocks or challenges** | Aggressive anti-bot stacks (DataDome, PerimeterX, Cloudflare Turnstile, bank/broker fraud systems) fingerprint far more than `navigator.webdriver` — timing, canvas/WebGL, TLS, CDP-presence. Attach mode does not defeat these. | Accept it: do that site's task by hand. Don't try to out-engineer a fraud team. If you only need it occasionally, the in-browser extension route (Architecture B) is the least-detectable option, but even it isn't guaranteed on hardened sites. |

---

## 8. Safety & terms-of-service reality

- The debug port must stay bound to **localhost (`127.0.0.1`) only** — never `0.0.0.0`, never forwarded or tunneled over a network. `--remote-debugging-port=9222` binds to loopback by default; leave it that way.
- **An open CDP port is full, unauthenticated control of that browser**: anything running on the machine that can reach `localhost:9222` can read every cookie and act as you on every site you logged into. Treat it like an open, unauthenticated root shell — launch the port only when you need it, and close that Chrome when you're done (`pkill -f "user-data-dir=$HOME/.config/agent-chrome"`). Don't run this on a shared machine where others can reach your loopback.
- **Never automate credential entry.** Passwords and 2FA are always typed by you, by hand. This is both the thing that keeps Google from blocking you and the thing that keeps your account safe. The agent only ever operates a session that is *already* logged in.
- Automating a logged-in session can violate some sites' terms of service — **Google's terms specifically disallow automated access to your account**, which is exactly why the "not secure" wall exists. Automating your *own* account for your *own* convenience is low-risk in practice, but the downside if flagged (temporary block up to suspension) is real. Keep it human-paced and scoped to sites you're comfortable with.