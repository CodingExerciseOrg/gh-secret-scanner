# GitHub Actions Secret Scanner

A web app that connects to a GitHub organization, retrieves recent GitHub Actions workflow runs, downloads their logs, scans them for secrets, and displays findings grouped by repository and workflow run.

---

## Project structure

```
gh-secret-scanner-web/
├── src/
│   ├── main.py              ← FastAPI app entry point
│   ├── github_client.py     ← GitHub REST API wrapper
│   ├── auth.py              ← shared GitHub authentication helper
│   ├── scanner.py           ← scanner wrapper (mock / test)
│   ├── storage.py           ← JSON file persistence
│   ├── poller.py            ← background polling thread
│   └── api/
│       ├── routes_config.py   ← GET/POST /api/config
│       ├── routes_repos.py    ← GET /api/repos
│       ├── routes_findings.py ← GET/DELETE /api/findings
│       └── routes_status.py   ← GET /api/status
├── web/
│   ├── index.html           ← single-page UI
│   ├── app.js               ← fetch calls, tab switching, live status
│   └── style.css            ← styling
├── secret_scanner/
│   └── secret_scanner.py   ← standalone scanner
├── config/                  ← runtime state (auto-created, gitignored)
├── run.py                   ← entry point
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

The app generates and refreshes installation tokens automatically (tokens expire after 60 minutes; the app refreshes every 50 minutes).

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

> Note: `reload` is disabled in `run.py` by default. To enable auto-restart on file save during development, set `reload=True` in `run.py`.

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
   - choose PAT or GitHub App
   - if PAT, Paste your Personal Access Token
   - if GitHub App, enter App ID, Installation ID, private key
   - Choose scanner mode (`mock` for demo, `test` to use the included scanner script)
   - Click **Save & connect**
3. An immediate scan is triggered.
4. Use the **Repositories tab** to see discovered repos.
5. Use the **Findings tab** to see secrets found, grouped by repo and workflow run.

The status bar shows the poller status and countdown to the next automatic scan (every 1 minute by default, set in `src/poller.py`).

---

## Scanner modes

| Mode | Behavior |
|------|----------|
| `mock` | Built-in heuristics, no binary needed. Default and safe for demo/submission. |
| `test` | Calls `secret_scanner/secret_scanner.py` via subprocess. Leave the path blank to use the default location. |

Switch modes in the Setup tab or by editing `config/config.json` directly and restarting the app.

---

## Findings tab actions

| Button | Behavior |
|--------|----------|
| **↻ Refresh** | Reload findings from disk without triggering a new scan. |
| **🗑 Dismiss** | Clear findings display. Already-scanned runs are kept in `seen_runs.json` so they won't be rescanned. |
| **↺ Reset & re-scan** | Clear findings and reset `seen_runs.json`. All runs will be rescanned on the next poll. |

---

## Deploying to a Linux server

```bash
# Install dependencies
pip install -r requirements.txt --break-system-packages

# Create systemd service
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

Note: compiling is optional. The included `secret_scanner.py` script works directly in `test` mode without compilation.

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

No temp file is written. No external process is spawned. The log text
string is scanned directly inside the running Python process.

### Test mode

```
poller._poll_cycle()
  → scanner.scan_log()
    → scanner._write_temp()       # writes log text to a temp .txt file
    → scanner._invoke(tmp_path)   # launches secret_scanner.py via subprocess
        → subprocess.run(cmd)
            → SecretScanner.scan()        # inside secret_scanner.py
                → SecretScanner._scan_file()  # reads the temp .txt file
                    → HEURISTICS          # patterns in secret_scanner.py
            → prints JSON to stdout
        → json.loads(result.stdout)   # parent process parses the output
    → scanner._enrich()           # adds repo/run context to raw findings
    → scanner._cleanup()          # deletes the temp .txt file
```

The subprocess boundary means `secret_scanner.py` is fully independent
and could be replaced by any script or binary that accepts the same
command-line interface and returns JSON.