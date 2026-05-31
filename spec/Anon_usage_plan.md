# TokenTray Anonymous Usage Telemetry — Design Plan

> **Status:** Plan only. No code changes proposed in this document.
> **Author:** Drafted with Copilot CLI · 2026-05-25
> **Scope:** Add an *optional*, privacy-first, anonymous aggregate-usage
> ping from the TokenTray client to a server at `reboundman.com`, so the
> maintainer can see trends across the install base over time.

---

## 1. Goals & Non-Goals

### Goals
- Learn **aggregate** trends: # of active installs, app version mix, OS
  build mix, model/parser mix, weekly token volumes (bucketed), feature
  usage (history tab opened, coffee unlock used, etc.), crash rate
  (counts only, no stack traces with paths/usernames).
- Be **invisible** to the user when it's working. Be **invisible** when
  it's broken (zero error UX, zero blocking, zero retries that bloat
  resource use).
- Be **defensible** under GDPR / UK-GDPR / CCPA / Microsoft tenant
  policy without needing a privacy lawyer to bless every release.

### Non-Goals (hard "no"s)
- ❌ Any user-identifying data (no username, hostname, MAC, machine SID,
  AAD/AD tenant, repo paths, file paths, prompt text, completion text,
  CLI session IDs, Copilot subscription type, IP address persisted on
  the server beyond a TLS termination log truncated within 24h).
- ❌ Any data that could be **re-identified** by joining with another
  dataset the maintainer has access to (e.g., GitHub username, repo
  name, AAD UPN). The install ID must be derivable from **nothing**
  except a CSPRNG.
- ❌ Crash *contents* (stack traces, log tails). Crash *counts* only.
- ❌ Any data on first launch, ever, without explicit consent.
- ❌ Any back-channel that could be abused for remote
  config/kill-switch/silent updates. The ping is **outbound, write-only**.

---

## 2. Privacy Principles (the contract with the user)

These are stated verbatim in `README.md`, the in-app "About telemetry"
dialog, and `docs/PRIVACY.md`. Any future contributor who proposes to
weaken them needs to update all three places **plus** bump a
`PRIVACY_VERSION` constant that forces a re-consent prompt.

1. **Default off.** No data leaves the machine until the user opts in.
   See §6 for the opt-in/opt-out debate and the chosen position.
2. **No identity.** The only stable token we send is a random
   `install_id` generated locally with `secrets.token_hex(16)` (128-bit,
   not 64-bit — see §3.1). It is not derived from any hardware,
   network, account, or filesystem attribute.
3. **No content.** We never read or transmit prompt text, completion
   text, file paths, repo names, branch names, or shell command lines.
   We only send numeric counters and low-cardinality enums.
4. **Low cardinality.** Every dimension we report has a small, fixed
   value set published in `docs/PRIVACY.md`. New dimensions require a
   PR that touches that doc. This makes fingerprinting via dimension
   joins ineffective.
5. **Silent failure.** If the server is down, the cert is wrong, the
   user is offline, a corporate proxy MITM-blocks us, or the user
   blocked the domain at the firewall — we drop the payload, log
   nothing user-visible, and try again next sync window.
6. **Local deletion is real deletion.** Deleting the `install_id` file
   (or clicking "Reset telemetry ID" in Settings) instantly orphans
   every server row tied to that ID — we have no way to re-associate.
7. **Server deletion on request.** Settings → "Delete my telemetry"
   sends a one-shot DELETE with the current `install_id` and then
   rotates to a fresh ID. The server purges all rows for that ID
   within 24h. (See §5.4.)
8. **Open source ping.** The full payload schema, the exact bytes sent,
   and a "Show last payload" button in Settings are part of the
   product. No hidden fields, ever.

---

## 3. Client Design

### 3.1 Install ID — *please use 128-bit, not 64-bit*

The user spec said "64-bit GUID". A real GUID/UUID is 128 bits. A
64-bit random ID gives ~50 % collision probability at ~5 billion IDs
(birthday bound √2⁶⁴), which is fine for this install base — but a
128-bit UUIDv4 gives effectively zero collision risk forever, is the
ecosystem standard, and costs us nothing (16 bytes vs 8 bytes on the
wire). **Recommendation: UUIDv4 via `secrets.token_hex(16)` rendered
as 32 lowercase hex chars.** I will call this out in the plan-review
step so the user can override if they really want 64-bit.

Storage:
- File: `%LOCALAPPDATA%\TokenTray\telemetry_id.txt`
- Contents: one line, the hex string, no BOM, no trailing newline.
- Permissions: created with the default user ACL. We do not need to
  ACL-restrict it — leaking *your own* anonymous ID to other processes
  on your own box is not a meaningful threat.
- Creation: on first run **after the user opts in**, never before.
  On every subsequent run we read it; if the file is missing or
  malformed, we generate a fresh one (the previous server-side rows
  become permanently orphaned — that is the intended deletion path).

### 3.2 What we send (the **entire** payload schema)

JSON, UTF-8, sent as the body of one `POST` per sync window. Example:

```json
{
  "schema": 1,
  "install_id": "5f3c…hex32…b2",
  "sent_at_utc": "2026-05-25T15:00:00Z",
  "window": {
    "from_utc": "2026-05-18T00:00:00Z",
    "to_utc":   "2026-05-25T00:00:00Z"
  },
  "app": {
    "version": "0.5.2",
    "channel": "release",
    "package": "installer"
  },
  "platform": {
    "os": "windows",
    "os_major": "10",
    "arch": "x64",
    "python": "3.12",
    "qt": "6.7"
  },
  "tier": {
    "trial_active": false,
    "supporter": true
  },
  "usage": {
    "active_days": 5,
    "popups_opened": 12,
    "history_tab_opened": 3,
    "refreshes": 1140,
    "tokens_total_bucketed": "10M-100M",
    "sessions_observed_bucketed": "10-99",
    "turns_observed_bucketed": "100-999",
    "parsers_seen": ["copilot_cli", "agency"]
  },
  "errors": {
    "parser_failures": 0,
    "db_quarantines": 0,
    "uncaught_exceptions": 0
  }
}
```

Key choices:
- **Bucketed integers**, not raw counts, for anything that could be
  unique enough to fingerprint a heavy user. Buckets are powers-of-ten
  bands (`0`, `1-9`, `10-99`, `100-999`, …). Even my heaviest user
  becomes indistinguishable from anyone else in their bucket.
- **No timestamps finer than a day.** No "I was active at 03:14 UTC"
  patterns (those leak timezone and sleep schedule).
- **No free-form strings.** `parsers_seen` is an enum drawn from a
  closed set; unknown parsers map to `"other"`.
- **No counts of zero** can be omitted vs sent — that is itself a bit
  of fingerprint info, so the schema is *fixed shape*: every key is
  always present, defaulting to `0` / `false` / `"0"`.
- `sent_at_utc` is rounded to the hour to reduce clock-skew
  fingerprinting; `window.*` is rounded to the day.

### 3.3 What we explicitly do **not** collect

| Tempting field | Why it's banned |
|---|---|
| Hostname / username | Direct identifier. |
| Machine GUID / MAC / disk serial | Stable cross-install identifier. |
| IP address (client-side) | Not collected client-side; server log truncated <24h. |
| GitHub login / AAD UPN | Direct identifier. |
| Repo paths, branch names | Identifies employer + project. |
| Prompt / completion text | Confidentiality + IP + PII. |
| CLI session IDs | Joinable with `~/.copilot/logs` if those ever leak. |
| Exact token counts | High-entropy; fingerprintable. |
| Wall-clock activity timestamps | Reveals timezone + sleep schedule. |
| Stack traces | Often contain `C:\Users\<name>\…` paths. |
| Copilot subscription tier | Joinable with internal billing data. |
| Tenant ID | Identifies employer. |

### 3.4 Sync schedule & failure behavior

- **Cadence:** once per 7 days, measured from `last_sent_at_utc` stored
  in `%LOCALAPPDATA%\TokenTray\telemetry_state.json`. First send is
  *not* on first launch — wait until at least 7 days of trial data
  exist so we don't fingerprint "freshly-installed user".
- **Window selection:** roll-forward by full days. If the user was
  offline for 3 weeks, we send one ping covering the most recent
  completed 7-day window and discard the older two. We don't bank
  unsent data — banking creates a long-lived spool with PII risk if
  the schema ever drifts.
- **Background thread, not main loop.** Send on a `QThread` or a plain
  `threading.Thread` with daemon=True. Never block the tray refresh,
  never block app exit (set a 5-second join timeout; if it hasn't
  finished, drop it).
- **Network timeout:** 10 s connect, 10 s read. One attempt. No
  retries. (Retries → spool growth → privacy regression.)
- **Failure handling:** `try/except Exception: pass`. We do *not* log
  the failure to `tray_app.log` (logs leak via support bundles; a
  blocked telemetry endpoint is not actionable info anyway). We do
  store `last_attempt_at_utc` in `telemetry_state.json` so we don't
  hammer a broken endpoint.
- **Proxy honour:** use `urllib.request` with default proxy resolution
  (respects `HTTPS_PROXY`, WPAD, system proxy). If a corporate proxy
  refuses us, that's a feature, not a bug.

### 3.5 Transport

- `POST https://telemetry.reboundman.com/v1/ping`
- TLS 1.2+ (Python stdlib default).
- Optional but recommended: **certificate pinning** to the Let's
  Encrypt intermediate or the leaf SPKI hash, so a hostile
  corporate MITM proxy can't silently re-decrypt our payload. Pin
  failure → silent drop (same as any other network error).
- Content-Type `application/json`, gzip-encoded body (always; it's
  small enough that gzip overhead is fine, and consistent encoding
  reduces fingerprintability).
- `User-Agent: TokenTray/<version> (telemetry; opt-in)`. No platform
  string in UA — platform is in the payload where it's bucketed.
- **No cookies, no auth header, no API key.** Anyone can POST. We
  rate-limit on the server (§4.2) by IP /24 to absorb abuse without
  storing IPs long-term.

### 3.6 Client-visible controls

In Settings menu (right-click tray → Settings):

1. **"Send anonymous usage stats"** (checkbox, *default unchecked*).
   First time the user ticks it, show the consent dialog (§6.3).
2. **"Telemetry → Show last payload…"** — opens a read-only window
   with the exact JSON we last sent (or "Never sent.").
3. **"Telemetry → Reset my ID"** — generates a new `install_id` and
   marks all prior server rows as orphaned (we don't actively delete;
   they age out per §5.4).
4. **"Telemetry → Delete my data on server…"** — fires a one-shot
   `DELETE /v1/ping/<install_id>`, rotates the ID locally, and shows
   "Sent. Server will purge within 24 hours. You cannot verify this
   directly because we don't know who you are — that's the point."
5. **"Telemetry → Privacy policy…"** — opens
   `https://reboundman.com/tokentray/privacy`.

### 3.7 Uninstall behavior

The Inno Setup uninstaller already removes `%LOCALAPPDATA%\Programs\TokenTray`.
We additionally:
- Prompt: *"Also remove your local TokenTray data (history DB, telemetry
  ID)?"* — defaults to **yes** to err on the side of forgetting.
- If yes: also POST the delete-on-server request best-effort (5 s
  timeout, silent fail).

---

## 4. Server Design (`telemetry.reboundman.com`)

### 4.1 Stack (lightweight)

- Static-ish hosting won't cut it; we need a tiny write endpoint.
  Options, in order of "less ops":
  1. **Cloudflare Worker + D1** (SQLite at the edge). Free tier
     handles >> our volume. Logs are off by default. Decision:
     **recommended.**
  2. Fly.io / Render small instance running FastAPI + SQLite.
  3. Vercel/Netlify Function + Turso. Same shape as #1.
- Decision criterion: pick the option where access logs are **off by
  default** and IPs are **not retained**. Cloudflare Workers meets
  this best.

### 4.2 Endpoint contract

```
POST   /v1/ping              # body: telemetry payload (§3.2)
DELETE /v1/ping/<install_id> # purge all rows for that ID
GET    /v1/health            # 200 OK, no body, for monitoring
```

- `POST`: validate JSON against the schema, drop unknown fields
  (defence in depth — if a future client sends extra, we don't
  silently store it), upsert into `pings(install_id, week_starting,
  payload_jsonb)`. Idempotent on `(install_id, week_starting)` so
  retries can't double-count.
- Rate-limit: 60 req/hour per IP /24, enforced in-memory at the edge.
  No persistent per-IP storage.
- Response: always `204 No Content` on success, `204` on validation
  failure too (don't help attackers probe the schema). Real errors
  (5xx) only for genuine infra failures.

### 4.3 What the server stores

```
pings(
  install_id      TEXT  NOT NULL,    -- 32-hex from client
  week_starting   DATE  NOT NULL,    -- UTC Monday of payload window
  app_version     TEXT,
  app_channel     TEXT,
  os              TEXT,
  os_major        TEXT,
  arch            TEXT,
  tier_supporter  INTEGER,
  tier_trial      INTEGER,
  active_days     INTEGER,
  popups_opened   INTEGER,           -- already bucketed by client
  ...
  received_at     TIMESTAMP NOT NULL DEFAULT now(),
  PRIMARY KEY (install_id, week_starting)
)
```

What's **not** stored:
- IP address (TLS terminator log is off; if on, retain ≤24h).
- User-Agent string (we don't trust it; reconstruct from
  `app.version` in payload).
- Geo / ASN lookups.
- Any raw JSON for fields outside the schema.

### 4.4 Retention

- Raw `pings` rows: **18 months**, then hard-delete via cron.
- Aggregated rollups (`weekly_active_installs`, `version_share`,
  `tokens_band_distribution`): kept indefinitely. Rollups are
  computed nightly from `pings` and contain **no `install_id`**.
- DELETE endpoint: removes all rows for an `install_id` synchronously.
  Aggregates rebuild nightly so the user's contribution to past
  rollups fades within a day.

### 4.5 Public dashboard

- `https://reboundman.com/tokentray/stats` shows aggregate charts:
  active installs over time, version adoption, OS mix, token-band
  histogram. **No row-level data is ever published.**
- Minimum cell size: any chart cell with fewer than `k=5` distinct
  `install_id`s is suppressed (shown as "<5"). This is k-anonymity
  for the public surface.

---

## 5. Threat Model

| Threat | Mitigation |
|---|---|
| Server compromise leaks DB | Rows contain no PII; worst case is "this random hex used TokenTray". |
| TLS MITM by hostile proxy | Cert pinning (§3.5); on failure, silent drop. |
| Malicious client floods endpoint | Edge rate-limit per IP /24; payload size cap 8 KB. |
| Future maintainer adds PII | `PRIVACY_VERSION` bump forces re-consent; CI lint rejects payload schema changes that aren't reflected in `docs/PRIVACY.md`. |
| Fingerprinting via dimension join | Closed enums + bucketed counters + day-resolution timestamps. |
| Re-identification via cross-org join | We hold no other dataset with `install_id`s; install_id is pure CSPRNG. |
| User wants out after sending | DELETE endpoint + local ID rotation; aggregates rebuild nightly. |
| Telemetry breaks the app | Background thread, hard timeout, silent except, no retries. |
| Telemetry breaks support diagnostics | We do not write telemetry events to `tray_app.log`. |

---

## 6. Opt-in vs Opt-out — the central decision

### 6.1 What each approach gets you

| | Opt-in (default OFF) | Opt-out (default ON, prompt) | Opt-out (default ON, no prompt) |
|---|---|---|---|
| Sample size | Smallest (5-15 % typical) | Medium (60-80 %) | Largest (>95 %) |
| Legal posture | Bulletproof in EU/UK/CA | Defensible if consent UX is clean | Risky under GDPR Art. 6; banned under ePrivacy for non-essential cookies/identifiers |
| User-trust posture | Best | OK if the prompt is honest | Worst |
| Maintainer reputation | Best | OK | Reputational risk in HN/Reddit threads |
| Open-source norm | Matches Helix, Zed, Neovim plugins | Matches VS Code (with strong UX) | Matches no respected OSS project |

### 6.2 Recommendation: **opt-in, default OFF.**

The user said *"I don't want to get in any trouble."* That sentence
alone settles it. The cost of opt-in is sample-size — fine, because:
- Trend direction matters more than absolute counts here.
- Even 10 % of installs is enough signal for the questions the
  maintainer actually wants to answer (is anyone using parser X? is
  version Y growing?).
- An opt-in cohort is self-selected toward power users, which is
  arguably *more* useful for product decisions than a representative
  sample of installs that launched once and quit.

### 6.3 First-run consent UX

On the **second** launch of any version that ships telemetry (not the
first launch — first launch is for the app to work, not for asking
favors), show a one-time dialog:

> **Help improve TokenTray?**
>
> TokenTray can send anonymous, aggregate usage stats once a week so
> the maintainer can see which features are actually used.
>
> - We send a random ID, your app version, OS family, and bucketed
>   counts. **No identity, no content, no file paths, no IPs.**
> - You can read the exact payload in Settings → Telemetry → Show
>   last payload.
> - You can turn this off or delete your data at any time.
>
> [ ] Send anonymous usage stats
>
> &nbsp;&nbsp;&nbsp; [ Read the full privacy policy ]  &nbsp; [ Not now ]  &nbsp; [ Save ]

- **Default unchecked.** "Not now" closes without enabling and the
  dialog never reappears automatically (it lives only in the
  Settings menu after that). "Save" with checkbox ticked is the
  *only* path that enables telemetry.
- Re-shown only when `PRIVACY_VERSION` increments.

### 6.4 What we do for users who never opt in
- Generate **nothing**. No `install_id` file, no `telemetry_state.json`,
  no network calls, no DNS lookup of `telemetry.reboundman.com`.
- The Settings → Telemetry submenu still works (so the user can
  inspect, change their mind, etc.) but its "Show last payload" says
  "Never sent."

---

## 7. Legal / Compliance Notes

- **GDPR / UK-GDPR**: A random per-install ID *can* be "personal data"
  under recital 26 if it's reasonably linkable to a person. With
  opt-in consent + Art. 6(1)(a) basis + the controls in §3.6 + the
  DELETE endpoint in §4.2, we're on the right side of the line. The
  `install_id` is not derived from anything that links to the
  natural person.
- **CCPA**: opt-in for telemetry exceeds CCPA's opt-out requirement.
- **Microsoft tenant policy** (the maintainer is an MS employee
  shipping a personal project): nothing in this design causes
  TokenTray to phone home with anything that could be construed as
  Microsoft data, Copilot prompt/completion content, or employee
  telemetry. The bucketed counts are about TokenTray's own UI.
- **DPA / privacy policy**: publish `https://reboundman.com/tokentray/privacy`
  as a plain-English page covering: what we collect, why, retention,
  the DELETE workflow, contact for inquiries. Link it from the
  consent dialog, the Settings submenu, and `README.md`.

---

## 8. Pros & Cons Summary

### Pros
- Maintainer gains real signal about real users without invading them.
- Architecturally simple: ~150 lines of client code, ~50 lines of
  Worker code, no auth system, no PII vault to defend.
- Failure is invisible by design — no support burden from broken
  telemetry.
- The opt-in posture is a marketing asset: README can say "we ask
  permission; default off; here's exactly what we send".

### Cons / risks (and the mitigation chosen)
- **Smaller sample.** → Acceptable; trends, not census.
- **Some users will see "telemetry" and never trust the app again,
  even if it's off by default.** → README + privacy page lead with
  "off by default; here's the exact payload". Be loud about it.
- **A future contributor could regress the privacy posture.** →
  `PRIVACY_VERSION` constant + CI check that any change to the
  payload schema also updates `docs/PRIVACY.md` + re-consent dialog
  fires when `PRIVACY_VERSION` bumps.
- **Server costs / abuse.** → Cloudflare Worker free tier + IP /24
  rate-limit + 8 KB payload cap.
- **Cert-pinning maintenance.** → Pin to LE intermediate (rotates
  yearly with predictable lead time); keep fallback to system-CA
  validation behind a `STRICT_PIN` build flag so a botched cert
  rotation doesn't black-hole all clients.
- **"64-bit GUID" in the spec is non-standard.** → Plan recommends
  128-bit UUIDv4. Flag for user override.

---

## 9. Phased Implementation Roadmap *(when this plan is approved)*

1. **Phase 0 — Docs first.** Write `docs/PRIVACY.md` and the consent
   dialog copy. Get the user's sign-off on wording before any code.
2. **Phase 1 — Server.** Stand up the Cloudflare Worker + D1 with
   `/v1/health`, `/v1/ping`, `/v1/ping/<id>` DELETE. End-to-end
   curl tests. Publish privacy page.
3. **Phase 2 — Client plumbing (no UI yet).** `telemetry.py` module:
   ID generation, payload assembly, gzip POST, silent-fail thread.
   Unit-tested with a mock server. Behind a hard-coded
   `TELEMETRY_ENABLED = False` flag — no code path can turn it on yet.
4. **Phase 3 — Settings UI + consent dialog + "Show last payload"
   viewer.** Still gated; only QA can enable.
5. **Phase 4 — Public beta.** Flip the gate; ship v0.6.0 with the
   second-launch consent prompt. Watch `/v1/health` and the
   payload-validation reject rate for a week.
6. **Phase 5 — Public stats page.** Once we have ≥100 opted-in
   installs sending a few weeks of data, publish the dashboard.

---

## 10. Open questions for the user

1. **64-bit vs 128-bit install ID.** Recommend 128-bit UUIDv4. OK to
   override the spec?
2. **Opt-in vs opt-out.** Recommend opt-in, default OFF. Confirm?
3. **Hosting.** Recommend Cloudflare Workers + D1. Any preference
   against Cloudflare (e.g., already use Vercel / Fly / a personal
   VPS)?
4. **Domain.** `telemetry.reboundman.com` OK, or prefer something
   less obviously telemetry-shaped (`api.reboundman.com/t/...`)?
   I recommend the obvious name — hiding it would be a red flag if
   discovered.
5. **Cert pinning.** Recommend yes, with `STRICT_PIN` build-flag
   escape hatch. Confirm?
6. **Crash counts.** Recommend counts only, no traces. OK?
7. **Public dashboard.** Build it (§4.5) or keep stats private to
   the maintainer?
