# GitHub Actions Secret Scanner

A web app that connects to a GitHub organization, retrieves recent GitHub Actions workflow runs, downloads their logs, scans them for secrets, and displays findings grouped by repository and workflow run.

Supports **multiple concurrent users** — each user's configuration and findings are isolated by a credential-derived identity stored in a signed browser cookie.

---

## Project structure

```
gh-secret-scanner-web/
├── src/
│   ├── main.py                ← FastAPI app entry point
│   ├── github_client.py       ← GitHub REST API wrapper
│   ├── auth.py                ← shared GitHub authentication helper
│   ├── identity.py            ← credential hashing + signed cookie helpers
│   ├── identity_registry.py   ← per-user Storage and Poller management
│   ├── scanner.py             ← scanner wrapper (mock / test)
│   ├── storage.py             ← per-user JSON file persistence
│   ├── poller.py              ← background polling thread
│   └── api/
│       ├── routes_config.py   ← GET/POST /api/config
│       ├── routes_repos.py    ← GET /api/repos
│       ├── routes_findings.py ← GET/DELETE /api/findings
│       └── routes_status.py   ← GET /api/status
├── web/
│   ├── index.html             ← single-page UI
│   ├── app.js                 ← fetch calls, tab switching, live status
│   └── style.css              ← styling
├── secret_scanner/
│   └── secret_scanner.py      ← standalone test scanner (for tests only, not production ready)
├── config/
│   ├── secret.key             ← server signing secret (auto-created)
│   └── users/
│       └── <identity-hash>/   ← per-user data folder (auto-created)
│           ├── config.json
│           ├── findings.json
│           └── seen_runs.json
├── run.py                     ← entry point
├── requirements.txt
└── README.md
```

---

## Requirements

- Python 3.10+
- One of the following GitHub authentication methods:

**Option A — Personal Access Token (PAT)**

A classic PAT or fine-grained token with these scopes:
- `repo` (or `public_repo` for public-only orgs)
- `workflow`

**Option B — GitHub App**

A GitHub App installed on the target organization with these permissions:
- Repository permissions → **Actions**: Read-only
- Repository permissions → **Metadata**: Read-only

You will need:
- **App ID** — shown on the App settings page
- **Installation ID** — found in the URL after installing the App on your org (`/settings/installations/<id>`)
- **Private key** — a `.pem` file generated from the App settings page

The app generates and refreshes installation tokens automatically (tokens expire after 60 minutes; tokens are refreshed automatically before expiry).

---

## Install

```bash
pip install -r requirements.txt
```

On Ubuntu 24.04:
```bash
pip install -r requirements.txt --break-system-packages
```

---

## Run locally

From the project root:

```bash
python run.py
```

Then open **http://localhost:8000** in your browser.

To stop: `Ctrl+C` in the terminal.

> Note: `reload` is disabled in `run.py` by default. To enable auto-restart on file save during development, set `reload=True` in `run.py`. Avoid doing this in production — the file watcher spawns a second process that can cause duplicate scans.

---

## API docs

FastAPI auto-generates interactive API documentation at:

- **http://localhost:8000/docs** — Swagger UI
- **http://localhost:8000/redoc** — ReDoc

---

## How to use

1. Open **http://localhost:8000**
2. In the **Setup tab**:
   - Enter your GitHub organization name
   - Choose PAT or GitHub App authentication
   - If PAT: paste your Personal Access Token
   - If GitHub App: enter App ID, Installation ID, and private key
   - Choose scanner mode (`mock` for demo, `test` to use the included scanner script)
   - Click **Save & connect**
3. An immediate scan is triggered automatically.
4. Use the **Repositories tab** to see discovered repos.
5. Use the **Findings tab** to see secrets found, grouped by repo and workflow run.

The status bar shows the poller status and countdown to the next automatic scan (every 30 minutes hardcoded, the interval is currently fixed at 30 minutes in src/poller.py`).

---

## Multi-user support

Multiple users can use the same running instance simultaneously. Each user's data is fully isolated:

- When a user saves their config, a short hash is derived from their **credential combined with the organization name** (token + org, or App ID + Installation ID + private key + org).
- That hash becomes the name of their data folder: `config/users/<hash>/`.
- Including the org in the hash means the same credential used against different organizations produces different data folders — a single user can manage multiple organizations independently, each with its own findings and scan state.
- A signed cookie (`identity`) is set in the browser so subsequent requests are automatically routed to the correct data folder.
- The cookie is signed with a server-side secret (`config/secret.key`) so it cannot be forged or tampered with. The hash reveals nothing about the credential or organization.
- Each user+org combination gets its own independent poller thread, findings list, and seen-runs tracker.

**Cookie lifetime:** 90 days. Clearing browser cookies or switching browsers requires re-saving config to re-establish the identity. Switching to a different organization in the Setup tab automatically switches to that org's data folder — no manual action needed.

**Data isolation:** Users cannot see each other's findings, repositories, or configuration. There is no shared state between identities.

---

## Scanner modes

| Mode | Behavior |
|------|----------|
| `mock` | Built-in heuristics, no binary needed. Default and safe for demo/submission. |
| `test` | Calls `secret_scanner/secret_scanner.py` via subprocess. Leave the path blank to use the default location. |

Switch modes in the Setup tab. Each user can independently choose their scanner mode.

> Note: in `test` mode, `scanner_path` is resolved relative to the project root. The default (`secret_scanner/secret_scanner.py`) works without any extra configuration.

---

## Findings tab actions

| Button | Behavior |
|--------|----------|
| **↻ Refresh** | Reload findings from disk without triggering a new scan. |
| **🗑 Dismiss** | Clear findings display. Already-scanned runs are kept in `seen_runs.json` so they won't be rescanned. |
| **↺ Reset & re-scan** | Clear findings and reset `seen_runs.json`. All runs will be rescanned on the next poll. |

These actions only affect the currently logged-in user's data.

---

## Deploying to a Linux server

```bash
pip install -r requirements.txt --break-system-packages
nano /etc/systemd/system/gh-scanner.service
```

Paste:
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

Nginx config:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/gh-scanner /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

For HTTPS:
```bash
certbot --nginx -d your-domain.com
```

> **Security note for production:** The `config/` directory contains user tokens and private keys. Ensure it is not served by the web server and is readable only by the application user.

---

## Compiling the scanner binary (optional)

```bash
# Windows
pip install pyinstaller
cd secret_scanner
python -m PyInstaller --onefile secret_scanner.py
# Output: secret_scanner/dist/secret_scanner.exe

# Ubuntu
pip install pyinstaller --break-system-packages
cd secret_scanner
python -m PyInstaller --onefile secret_scanner.py
# Output: secret_scanner/dist/secret_scanner
```

The compiled binary can be used in `test` mode by setting `scanner_path` to the binary path in the Setup tab. Compilation is optional — `secret_scanner.py` works directly in `test` mode without it.

---

## Scanner call chains

### Mock mode

```
poller._poll_cycle()
  → scanner.scan_log()
    → _scan_text(log_text)        # regex runs in-process, no subprocess
        → _HEURISTICS             # patterns defined at top of scanner.py
    → scanner._enrich()           # adds repo/run context to raw findings
```

No temp file is written. No external process is spawned.

### Test mode

```
poller._poll_cycle()
  → scanner.scan_log()
    → scanner._write_temp()       # writes log text to a temp .txt file
    → scanner._invoke(tmp_path)   # launches secret_scanner.py via subprocess
        → subprocess.run(cmd)
            → SecretScanner.scan()
                → SecretScanner._scan_file()
                    → HEURISTICS
            → prints JSON to stdout
        → json.loads(result.stdout)
    → scanner._enrich()           # adds repo/run context to raw findings
    → scanner._cleanup()          # deletes the temp .txt file
```

The subprocess boundary means `secret_scanner.py` is fully independent and could be replaced by any script or binary that accepts the same CLI interface and returns JSON.

## Known limitations

### 1. Per-identity server-side bookkeeping may remain after idle pollers are stopped

When an identity becomes idle, its background poller is stopped after the configured timeout. However, some in-memory registry bookkeeping for that identity may remain until the application is restarted.

Impact:
- this does **not** affect the correctness of scans
- this does **not** corrupt findings or configuration data
- this does **not** keep polling active after the poller has been stopped
- this mainly results in minor server-side memory/bookkeeping growth for previously used identities

For the current exercise/demo scope, this limitation is acceptable.

### 2. Previous identities may continue polling temporarily after the user switches identities

When a user switches to another organization or credential set, the previously active identity is not stopped immediately. Instead, its background poller continues running until it becomes idle and is stopped by the poller timeout logic.

Impact:
- the previous identity may continue polling GitHub for a limited time
- this can cause temporary overlap between old and new identity pollers
- this may generate some extra background API calls and thread usage during that window

For the current exercise/demo scope, this behavior is an intentional tradeoff and is acceptable.
