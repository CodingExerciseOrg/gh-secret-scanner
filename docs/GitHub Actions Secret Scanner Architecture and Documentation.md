# GitHub Actions Secret Scanner — Architecture & Developer Documentation

---

## Overview

A web application that connects to a GitHub organization, polls GitHub Actions workflow runs every 30 minutes, downloads their logs, scans them for accidentally leaked secrets, and displays findings grouped by repository and workflow run.

The application is built with **FastAPI** (Python backend) and **plain HTML/JS** (frontend). It supports multiple independent users — each identified by a hash of their GitHub credentials — with fully isolated data per user.

---

## Project Structure

```
gh-secret-scanner-web/
├── run.py                        ← entry point
├── manage_users.py               ← (removed) CLI helper (not in this version)
├── requirements.txt
├── secret_scanner/
│   └── secret_scanner.py        ← standalone scanner binary (can be compiled)
├── src/
│   ├── main.py                  ← FastAPI app, wires everything together
│   ├── identity.py              ← credential hashing + signed cookie
│   ├── identity_registry.py     ← per-identity Storage and Poller instances
│   ├── storage.py               ← per-identity JSON file persistence
│   ├── auth.py                  ← GitHub authentication helper
│   ├── github_client.py         ← GitHub REST API wrapper
│   ├── poller.py                ← background polling thread
│   ├── scanner.py               ← scanner wrapper (mock / test modes)
│   └── api/
│       ├── routes_config.py     ← GET/POST /api/config
│       ├── routes_repos.py      ← GET /api/repos
│       ├── routes_findings.py   ← GET/DELETE /api/findings
│       └── routes_status.py     ← GET /api/status
├── config/
│   ├── secret.key               ← server signing secret (auto-generated)
│   └── users/
│       └── <identity_hash>/
│           ├── config.json
│           ├── findings.json
│           └── seen_runs.json
└── web/
    ├── index.html
    ├── app.js
    └── style.css
```

---

## File and Class Reference

### `run.py`

Entry point. Changes the working directory to `src/` before starting uvicorn so all imports resolve correctly regardless of platform. On Windows, uvicorn spawns a subprocess that does not inherit `sys.path` changes — using `os.chdir()` is the reliable fix.

```bash
python run.py
```

---

### `src/main.py`

FastAPI application shell. Responsible for:

- Initialising the `identity` module (loads or creates `config/secret.key`)
- Creating the `IdentityRegistry` instance shared across all routes
- Registering all API routers
- Mounting the static frontend files from `web/`
- Stopping all pollers cleanly on shutdown via the `lifespan` context

No business logic lives here — it wires components together.

---

### `src/identity.py`

**The core of the multi-user system.** No login, no passwords.

**How identity works:**
1. When the user saves their GitHub config, a 16-character SHA-256 hash is derived from their credential
2. That hash becomes the name of their data folder: `config/users/<hash>/`
3. The hash is stored in an `HttpOnly` cookie, signed with a server-side HMAC secret
4. On every subsequent request, the cookie is verified and the hash is used to load the correct data folder

**Key functions:**

| Function | Purpose |
|----------|---------|
| `init(config_dir)` | Called once at startup. Loads or generates `config/secret.key` |
| `credential_hash(config)` | Derives the 16-char identity hash from the config dict |
| `make_cookie_value(hash)` | Signs the hash and returns a cookie-safe string |
| `read_cookie_value(cookie)` | Verifies the signature and returns the hash, or `None` if invalid |

**Hash derivation:**
- Token auth: `sha256(token)[:16]`
- App auth: `sha256(app_id + "|" + installation_id + "|" + private_key)[:16]`

The hash is opaque — it reveals nothing about the original credential.

**Cookie security:**
The cookie is signed using `itsdangerous.URLSafeSerializer` with HMAC-SHA1. If anyone tampers with the cookie value, signature verification fails and the request gets empty defaults. The server secret is stored in `config/secret.key` and never leaves the server.

---

### `src/identity_registry.py` — `IdentityRegistry`

Thread-safe registry that lazily creates and manages per-identity resources.

Each identity hash gets exactly one `Storage` instance and one `Poller` thread. Instances are created on first access and reused for all subsequent requests from the same identity.

| Method | Purpose |
|--------|---------|
| `get_storage(identity)` | Returns (creating if needed) the Storage for an identity |
| `get_poller(identity)` | Returns (creating and starting if needed) the Poller for an identity |
| `rebuild_scanner(identity)` | Rebuilds the Scanner from current config, resets auth state, triggers immediate poll |
| `stop_all()` | Stops and joins all pollers — called on server shutdown |

---

### `src/storage.py` — `Storage`

Reads and writes JSON files for one identity. Constructed with an explicit `data_dir` path — the registry is responsible for passing the correct directory.

**Thread safety:** An `RLock` serializes all reads and writes. `RLock` (re-entrant) rather than plain `Lock` is used because `append_findings()` calls `load_findings()` and `save()` while holding the lock.

**Atomic writes:** All writes go to a `.tmp` file first, then `tmp.replace(dest)` — an atomic rename on both Linux and Windows. Concurrent readers never see a partial file.

**Files managed:**

| File | Contents |
|------|----------|
| `config.json` | Org, auth method, token/app credentials, scanner mode |
| `findings.json` | All accumulated findings for this identity |
| `seen_runs.json` | Set of already-scanned run keys (prevents re-scanning) |

---

### `src/auth.py`

Shared GitHub authentication helper used by both the poller and the repos API route.

**`make_client(storage)`** — returns an authenticated `GitHubClient`:
- Token auth: reads `config["token"]` directly
- App auth: checks `config["token_expires_at"]`; if the cached token is still fresh, reuses it; otherwise calls `GitHubClient.from_app()` to generate a new one, persists it with a new expiry timestamp, and returns the client

The refresh threshold is 50 minutes (`APP_TOKEN_TTL_SECONDS`) — 10 minutes before the GitHub App installation token's 60-minute expiry.

---

### `src/github_client.py` — `GitHubClient`

Thin wrapper around the GitHub REST API v3.

| Method | Purpose |
|--------|---------|
| `__init__(token)` | Creates a `requests.Session` with Bearer auth header |
| `from_app(app_id, installation_id, private_key)` | GitHub App auth — signs a JWT, exchanges it for an installation token, returns `(client, token)` tuple |
| `list_repos(org)` | Lists all repos in an org, handles pagination |
| `list_recent_runs(org, repo, limit)` | Returns the 10 most recent workflow runs for a repo |
| `download_logs(org, repo, run_id)` | Downloads the log zip archive, extracts per-step `.txt` files, returns concatenated text with `=== Step Name ===` section headers |

Log extraction prefers per-step files (e.g. `build/5_Deploy.txt`) over the merged top-level file (`0_build.txt`) because step files carry the step name in the filename. Infrastructure steps (Set up job, Complete job, Post actions) are filtered out.

---

### `src/poller.py` — `Poller`

Background daemon thread. One instance per identity, managed by `IdentityRegistry`.

**Poll cycle (every 30 minutes, or immediately when triggered):**
1. Load config and authenticate via `auth.make_client()`
2. List all repos in the org
3. For each repo, fetch the 10 most recent workflow runs
4. Skip runs already in `seen_runs.json`
5. Download logs for new runs
6. Pass logs to `Scanner.scan_log()`
7. Save findings and update `seen_runs.json`
8. Call `on_update(message)` at each step — the message appears in the UI status bar

**Key methods:**

| Method | Purpose |
|--------|---------|
| `trigger_now()` | Wakes the thread immediately (e.g. after config save) |
| `stop()` | Sets stop event and wakes force event so thread exits cleanly |
| `set_scanner(scanner)` | Replaces the scanner at runtime without restarting the thread |
| `reset_auth_state()` | Sets `token_expires_at = 0` so next poll forces a fresh token exchange |
| `seconds_until_next_poll()` | Returns countdown for the UI status bar |

**Run deduplication:** A run is added to `seen_runs.json` only after it is successfully scanned. If a run is in progress (no logs yet), it is retried on the next poll. If a completed run has no logs (expired), it is marked as seen immediately to avoid infinite retries.

---

### `src/scanner.py` — `Scanner`

Wrapper that invokes secret detection and returns enriched findings.

**Two modes:**

| Mode | Behaviour |
|------|-----------|
| `mock` | Scans the log text directly in-process using regex heuristics. No subprocess, no temp file. Default and safe for demos. |
| `test` | Writes log text to a temp `.txt` file, invokes `secret_scanner/secret_scanner.py` via subprocess, parses its JSON stdout. |

**`scan_log(log_text, repo, run_id, run_name)`** — main entry point. Returns a list of finding dicts enriched with repo and run context.

**`_enrich(raw_findings, ...)`** — adds `repo`, `run_id`, `run_name` to each raw finding returned by the scanner.

**Path resolution:** In `test` mode, if `scanner_path` is a relative path it is resolved from the project root (`Path(__file__).parent.parent`), not the current working directory. This ensures it works correctly when running as a systemd service.

**Heuristics (both mock and test mode):**
`aws_access_key`, `aws_secret_key`, `github_token`, `bearer_token`, `base64_credentials`, `generic_password`, `generic_token`, `generic_api_key`, `generic_secret`, `generic_credential`

Infrastructure lines (e.g. `##[group]`, `Requesting a runner`) and `echo` command lines are skipped to reduce false positives.

---

### `secret_scanner/secret_scanner.py` — `SecretScanner`

Standalone scanner binary. Self-contained — no dependencies on the rest of the app.

**CLI interface:**
```bash
python secret_scanner.py scan /path/to/log.txt --format json
```

**Output (stdout):**
```json
{
  "scanner_version": "1.0.0",
  "source_file": "/path/to/log.txt",
  "findings": [
    { "line_number": 42, "secret_type": "generic_api_key", "matched_text": "api_key=abc123", "step": "Deploy" }
  ]
}
```

Can be compiled to a standalone executable:
```bash
pyinstaller --onefile secret_scanner.py
```

The `Scanner` class in `src/scanner.py` (`test` mode) calls this binary and parses its output. Swapping in a real proprietary scanner requires only changing `scanner_path` in config — the interface is identical.

---

### `src/api/routes_config.py`

**`GET /api/config`** — reads the identity cookie, loads and returns the current config (never returns the raw token or private key, only boolean flags `token_set` and `private_key_set`).

**`POST /api/config`** — validates and saves config, then:
1. For app auth: performs a live token exchange to validate credentials immediately
2. Derives the identity hash from the new credential
3. Saves config to `config/users/<hash>/config.json`
4. Sets the signed identity cookie in the response
5. Calls `on_config_saved(identity)` to rebuild the scanner and trigger an immediate poll

Sensitive fields (token, private key) are cleared from the browser after a successful save — the frontend replaces them with placeholder text.

---

### `src/api/routes_repos.py`

**`GET /api/repos`** — reads identity cookie, calls `auth.make_client()` (refreshing the app token if needed), lists all repos in the configured org. Returns repo name, visibility, language, default branch, and last-updated date.

---

### `src/api/routes_findings.py`

**`GET /api/findings`** — returns all findings for the current identity.

**`DELETE /api/findings?mode=dismiss|reset`**
- `dismiss`: clears findings only. Already-scanned runs remain in `seen_runs.json` so they won't be rescanned.
- `reset`: clears findings and `seen_runs.json`. All runs will be rescanned on the next poll.

---

### `src/api/routes_status.py`

**`GET /api/status`** — returns the latest poller status message, seconds until next poll, and total findings count. The frontend polls this every 3 seconds to update the status bar and countdown timer.

---

## Application Flow

### First use
```
User opens http://localhost:8000
  → no identity cookie → empty config form shown
  → user fills in org + token → clicks Save & connect
    → server validates credentials
    → derives identity hash: sha256(token)[:16]
    → saves config to config/users/<hash>/config.json
    → sets signed cookie: identity=<hash>.<signature>
    → triggers immediate poll
      → poller downloads logs → scanner finds secrets → findings saved
    → UI auto-refreshes findings within 3 seconds
```

### Returning user
```
User opens http://localhost:8000
  → browser sends identity cookie automatically
  → server verifies signature → loads config from correct folder
  → config form pre-populated with org, app_id, etc.
  → poller already running in background (started on first config save)
```

### Second user / different org
```
Different browser (or cleared cookies) opens http://localhost:8000
  → no cookie → empty form
  → saves different credentials → different hash → different config/users/<hash2>/
  → completely isolated findings, seen_runs, poller
```

---

## Authentication Methods

### Personal Access Token (PAT)

A classic or fine-grained GitHub token. Required scopes:
- `repo` (or `public_repo` for public-only orgs)
- `workflow`

The token is stored in `config.json` and used directly as a Bearer token on every GitHub API call. PATs do not expire on a short timer.

### GitHub App

A GitHub App installed on the target organization. Required permissions:
- Repository → Actions: Read-only
- Repository → Metadata: Read-only

**Authentication flow:**
1. On config save, `GitHubClient.from_app()` signs a short-lived JWT (9 minutes) using the App's RSA private key
2. The JWT is exchanged for an installation access token (valid 60 minutes) via `POST /app/installations/{id}/access_tokens`
3. The installation token is cached in `config.json` alongside `token_expires_at`
4. Before each GitHub API call, `auth.make_client()` checks `token_expires_at`. If the token is within 10 minutes of expiry (threshold: 50 minutes), a fresh token is generated and persisted automatically

The private key can be provided as a PEM string pasted directly into the UI, or as a path to a `.pem` file on the server.

---

## Multi-User / Multi-Organisation Support

There is no login system. Instead, **the GitHub credential itself is the identity**.

When a user saves config, `identity.credential_hash(config)` computes:

```
SHA-256(token)[:16]                              — for PAT auth
SHA-256(app_id|installation_id|private_key)[:16] — for App auth
```

This hash:
- Becomes the data folder name: `config/users/<hash>/`
- Is stored in a signed `HttpOnly` cookie

**Isolation:** Each identity gets its own `config.json`, `findings.json`, `seen_runs.json`, `Storage` instance, and `Poller` thread. Different users connecting to different orgs with different credentials are completely isolated from each other.

**Cookie security:** The hash is signed with HMAC using a server-side secret (`config/secret.key`). Tampering with the cookie value causes signature verification to fail, returning empty defaults rather than another user's data.

**Practical scenarios:**
- Two people using the same org with the same token → same hash → same data (shared view)
- Two people using different orgs or different tokens → different hashes → completely isolated
- Same user, different browser or cleared cookies → no cookie → empty form until they re-enter credentials

---

## Limitations

| Limitation | Detail |
|-----------|--------|
| No access control | Anyone who can reach the server URL can submit credentials and access findings. Add nginx HTTP Basic Auth if the server is public. |
| Stale pollers | Pollers for inactive identities are automatically stopped after ~1 hour of inactivity via a background reaper thread. However, identity data folders are not deleted and remain on disk indefinitely. |
| PAT stored in plaintext | The token is stored in `config.json`. Restrict file permissions: `chmod 700 config/users/` |
| Private key stored in plaintext | Same as above. The PEM is stored as-is in `config.json`. Consider using a file path instead of pasting the key contents. |
| `secret.key` is critical | Losing `config/secret.key` invalidates all browser cookies. Users will need to re-enter their credentials. Back it up. |
| 10 most recent runs per repo | `list_recent_runs()` fetches `per_page=10`. Older runs are never scanned. In practice the impact depends on polling frequency: if a repo produces fewer than 10 runs per 30-minute poll interval, every run will be seen. The limit only becomes a real gap in repos with very high CI throughput. Raising the limit to 100 (GitHub's API maximum) costs nothing in extra API calls — `per_page=100` is still a single request — but the **first poll** after the change will download up to 90 additional log archives per repo, which can be slow and may approach GitHub API rate limits for large organisations. Subsequent polls are unaffected since already-scanned runs are skipped. For heavily loaded repos or organisations, full pagination of workflow runs should be added (mirroring how `list_repos` already handles pagination) to guarantee complete coverage. |
| Log expiry | GitHub expires workflow run logs after 90 days. Expired runs are marked as processed and skipped. |
| No WebSockets | Status updates are polled every 3 seconds. Findings appear within 3 seconds of the poller completing. |
| Single process | All pollers run as threads in one Python process. For large numbers of identities this could consume significant memory. |

---

## Deployment

### Local (VS Code / Windows)
```bash
pip install -r requirements.txt
python run.py
# Open http://localhost:8000
```

### Linux server (systemd)
```ini
[Unit]
Description=GitHub Actions Secret Scanner
After=network.target

[Service]
WorkingDirectory=/opt/gh-secret-scanner-web
ExecStart=/usr/bin/python3 /opt/gh-secret-scanner-web/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable gh-scanner
systemctl start gh-scanner
```

Nginx reverse proxy:
```nginx
server {
    listen 80;
    server_name scanner.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Add HTTPS:
```bash
certbot --nginx -d scanner.example.com
```

---

## API Reference

| Method | Endpoint | Auth required | Description |
|--------|----------|---------------|-------------|
| `GET` | `/api/config` | Cookie | Load current config (no secrets returned) |
| `POST` | `/api/config` | — | Save config, set identity cookie |
| `GET` | `/api/repos` | Cookie | List repositories for configured org |
| `GET` | `/api/findings` | Cookie | List all findings |
| `DELETE` | `/api/findings?mode=dismiss` | Cookie | Clear findings, keep seen-runs |
| `DELETE` | `/api/findings?mode=reset` | Cookie | Clear findings and seen-runs, retrigger scan |
| `GET` | `/api/status` | Cookie | Poller status, countdown, findings count |

Interactive API docs available at `http://localhost:8000/docs`.

