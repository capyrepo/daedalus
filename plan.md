# Security Module Plan: Malware Scanning & Intrusion Detection

## 1. Existing App Feature Summary

Daedalus is a personal home-server dashboard (FastAPI backend + vanilla JS/HTML frontend, no build step). Today it provides:

- **System monitoring** — CPU/memory/disk/process stats (`backend/collector.py` + `psutil`), shown on the main dashboard (`/`).
- **RAID array status** — parses `/proc/mdstat` for array health/sync progress (`backend/raid.py`).
- **Systemd services status** — up/down state of configured services.
- **Nginx status** — reverse-proxy health info.
- **Sites Manager** (`/sites`) — create/enable/disable/delete nginx-hosted subdomains from the browser, backed by a sudo-restricted helper script (`backend/sites.py` + `scripts/site-helper.sh`).
- **Web terminal** — an interactive shell in the browser over WebSocket, backed by a `tmux`-attached PTY (`backend/terminal.py`).
- **Authentication** — login against real Linux system accounts via PAM, cookie-based session (`backend/auth.py`).

## 2. New Feature: Overview & Scope

Add a **Security** module covering two capabilities, scoped for a personal home server (not an enterprise SIEM — no packet capture, no ML anomaly detection, no cross-host correlation):

1. **Malware/virus scanning** of the server's filesystem (hosted sites, home directories).
2. **Intrusion detection** — surfacing attempts by unauthorized parties to access the server (SSH brute force, banned IPs, unexpected open ports).

## 3. Tool Choices & Rationale

- **Malware scanning: ClamAV** (`clamdscan` against the `clamd` daemon when running, falling back to one-shot `clamscan`). It's the standard mature open-source AV engine with a CLI and signature-update pipeline (`freshclam`) — no Python bindings needed, consistent with how this repo already shells out to system tools (`nginx`, `systemctl`, `mdstat`) rather than adding library dependencies. Rejected alternatives: `rkhunter`/`chkrootkit` (rootkit-only, no real signature DB, poor machine-readable output), commercial engines (licensing/cloud dependency, inappropriate for a home server).
- **Intrusion detection: log parsing + optional fail2ban + connection snapshot**, not a SIEM:
  1. Parse SSH auth logs (`journalctl -u ssh`/`sshd`, falling back to `/var/log/auth.log`) for `Failed password`/`Invalid user` lines, aggregate failures per source IP to flag brute-force attempts.
  2. If `fail2ban` is installed, mirror its ban state via `fail2ban-client status <jail>` (read-only surfacing — not reimplementing its ban logic).
  3. Periodic snapshot of listening sockets (`psutil.net_connections`, same library `collector.py` already uses) to flag unexpected listeners.

## 4. Data Model (additions to `backend/models.py`)

```python
class ScanRequest(BaseModel):
    path: str | None = None   # defaults to /var/www if omitted

class ScanResult(BaseModel):
    id: int | None = None
    path: str
    started_at: str            # ISO timestamp
    finished_at: str | None
    status: str                 # "running" | "completed" | "failed"
    files_scanned: int
    threats_found: int
    infected_files: list[str]
    engine: str                 # "clamdscan" | "clamscan"
    error: str | None = None

class ScanSummary(BaseModel):
    last_scan: ScanResult | None
    scan_running: bool
    total_threats_all_time: int

class IntrusionEvent(BaseModel):
    id: int | None = None
    detected_at: str
    source_ip: str
    event_type: str              # "ssh_bruteforce" | "banned" | "unexpected_listener" | "suspicious_connection"
    detail: str
    severity: str                # "info" | "warning" | "critical"

class BannedIP(BaseModel):
    ip: str
    jail: str                    # e.g. "sshd"
    banned_at: str | None = None

class NetworkListener(BaseModel):
    proto: str                   # tcp/udp
    local_port: int
    pid: int | None
    process_name: str | None
    is_expected: bool

class SecurityStatus(BaseModel):
    scan_summary: ScanSummary
    recent_events: list[IntrusionEvent]
    banned_ips: list[BannedIP]
    unexpected_listeners: list[NetworkListener]
    clamav_available: bool
    fail2ban_available: bool
```

## 5. Persistence (`backend/database.py`)

This module currently has an unused `aiosqlite` connection helper (`get_db()`) with no tables anywhere in the repo. This feature is the first real consumer. Add an `init_db()` bootstrap, called from `main.py`'s `lifespan()`:

```sql
CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    files_scanned INTEGER NOT NULL DEFAULT 0,
    threats_found INTEGER NOT NULL DEFAULT 0,
    infected_files TEXT NOT NULL DEFAULT '[]',   -- JSON-encoded list
    engine TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS intrusion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    source_ip TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    severity TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intrusion_ip_time ON intrusion_events(source_ip, detected_at);
```

## 6. Backend Module (`backend/security.py`)

New file, following `collector.py`'s cache+periodic-refresh pattern for cheap checks and `sites.py`'s subprocess pattern for shelling out:

- `HELPER = "/usr/local/bin/webmonitor-security-helper"`, `SCAN_ALLOWLIST = [Path("/var/www"), Path("/home")]`.
- `async def startup()` — probe `clamav_available`/`fail2ban_available` via `shutil.which`, run one initial intrusion refresh, load last scan summary from DB.
- `async def run_forever()` — collector-style loop refreshing the intrusion snapshot every `INTRUSION_INTERVAL` (45s), wrapped in try/except so one failure doesn't kill the loop.
- `async def trigger_scan(path) -> ScanResult` — validates `path` resolves under `SCAN_ALLOWLIST`, rejects if a scan is already running, inserts a `status="running"` DB row, spawns `_run_scan()` as a background task, returns immediately so the UI can poll.
- `async def _run_scan(path, row_id)` — runs `clamdscan -r <path>` (or `clamscan` fallback) via `asyncio.create_subprocess_exec`, parses the scan summary + infected-file list, updates the DB row and cache.
- `async def _refresh_intrusion()` — parses auth logs for brute-force IPs, mirrors fail2ban bans, snapshots unexpected listeners; deduplicates repeated alerts per IP+event_type within a 10-minute window so the background loop doesn't flood the events table.
- `get_cached(key)` / `get_scan_status()` / `list_scan_history()` / `get_status()` — read accessors for the API routes.
- `ban_ip(ip)` / `unban_ip(ip)` — validate IP format (`ipaddress.ip_address`), call the privileged helper. Phase 3 only.

**Note:** start scanning/log-reading unprivileged first — `/var/www` and the app's own home directory are likely readable without root. Only route an operation through the sudo helper if it actually hits a `PermissionError` in practice; don't default everything to privileged out of caution.

## 7. API Routes (`backend/main.py`)

Page route (manual session check, same pattern as `/sites`):

```python
@app.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=302)
    return _SECURITY_HTML
```

JSON routes (all using the existing `Depends(require_auth)` + `isinstance(auth, RedirectResponse)` boilerplate):

| Method | Path | Purpose | Phase |
|---|---|---|---|
| GET | `/api/security/status` | Full `SecurityStatus` snapshot | 1 (scan fields), 2/3 (rest) |
| POST | `/api/security/scan` | Trigger a scan (`ScanRequest` body), 202 + scan id, 409 if already running | 1 |
| GET | `/api/security/scan/{scan_id}` | Poll one scan's `ScanResult` | 1 |
| GET | `/api/security/scans` | Scan history (last 20) | 1 |
| GET | `/api/security/events` | Recent `IntrusionEvent`s | 2 |
| GET | `/api/security/banned` | Currently banned IPs | 3 |
| POST | `/api/security/ban` | Ban an IP (`{ip: str}`) | 3 |
| POST | `/api/security/unban` | Unban an IP | 3 |

`_SECURITY_HTML` is loaded in `lifespan()` alongside the other page globals; `security.startup()` and a second `asyncio.create_task(security.run_forever())` are started/cancelled alongside the existing collector task.

## 8. Privileged Helper (`scripts/security-helper.sh`)

Mirrors `scripts/site-helper.sh`'s structure (`set -euo pipefail`, `case "$CMD" in ...)` dispatch, validate-then-act):

- `scan <path>` — `clamdscan -r <path>` as root, only for paths a `validate_path()` guard confirms (via `realpath`) are prefixed by `/var/www/` or `/home/` — rejects traversal and anything outside the allowlist.
- `read-auth-log` — `cat` the auth log, only needed if the app user can't already read it.
- `fail2ban-status <jail>` — `fail2ban-client status <jail>` (querying fail2ban typically requires root/socket access).
- `ban-ip <ip> [jail]` / `unban-ip <ip> [jail]` — `fail2ban-client set <jail> banip/unbanip <ip>`, with a `validate_ip()` regex guard before the IP ever reaches `fail2ban-client`.

Installed with a narrow `/etc/sudoers.d/webmonitor-security` entry, NOPASSWD scoped to the exact helper path only — same isolation principle as the existing sites helper.

## 9. Setup/Install Script (`scripts/setup-security.sh`)

No installer script exists anywhere in this repo today for system-level dependencies (the sites feature's installer was never committed). This feature adds the first one:

```bash
#!/usr/bin/env bash
set -euo pipefail
# 1. apt-get install -y clamav clamav-daemon fail2ban
# 2. systemctl enable --now clamav-freshclam clamav-daemon fail2ban
# 3. freshclam                                    # initial signature pull
# 4. install -m 0755 scripts/security-helper.sh /usr/local/bin/webmonitor-security-helper
# 5. write + validate /etc/sudoers.d/webmonitor-security (visudo -c)
# 6. usermod -aG adm <app-user>                    # unprivileged auth-log reads where possible
```

Idempotent and safe to re-run, matching the tone of `site-helper.sh`'s existing idempotency checks.

## 10. Frontend (`frontend/security.html` + `frontend/security.js`)

New page following `sites.html`/`sites.js` conventions exactly (dark theme, card/badge styling from `style.css`, the `api()` fetch wrapper with 401/403 → login redirect, and `esc()` from `app.js` for XSS-safe rendering of file paths/log lines/IPs):

- **Scan card** — path selector (default `/var/www`), "Scan Now" button, last scan result badge (green if clean, red if threats found), scan history list.
- **Intrusion events card** — recent events table (time, IP, type, severity badge), auto-refreshed.
- **Banned IPs card** (Phase 3) — list with unban action, manual ban-IP input.
- **Unexpected listeners card** — table of non-allowlisted open ports.

Add `<a href="/security" class="nav-link">Security</a>` to the header nav of every existing HTML page (`index.html`, `sites.html`, `login.html` where applicable) and the new page itself.

## 11. Phasing

- **Phase 1 — Malware scan (on-demand) + basic UI**: `models.py` additions (`ScanResult`/`ScanSummary`/`ScanRequest`), `scan_results` table, `security.py` scan functions, scan routes, scan-card UI, nav links. Skip the privileged helper initially — add it only if a real permission wall is hit.
- **Phase 2 — Intrusion log parsing + alerting**: `intrusion_events` table, remaining models, `_refresh_intrusion`/log-parsing/listener-snapshot logic, background task wiring, events + listeners UI.
- **Phase 3 — fail2ban integration + IP banning**: `BannedIP` model, fail2ban mirroring, helper `ban-ip`/`unban-ip`/`fail2ban-status` subcommands + sudoers, ban/unban routes and UI.

This order front-loads the lowest-risk, highest-value capability (scanning known file locations) before adding privileged network-defense actions that carry more blast-radius if misused.

## Out of Scope (for this document)

This is a design document only — no code, dependencies, or other files are changed as part of writing this plan.
