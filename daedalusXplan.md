# Plan: Sentinel — Standalone Malware Scanning & Intrusion Detection App

## 1. Context: What This Is (and Isn't)

This is **not** a module bolted onto the existing daedalus dashboard. It's a separate application — code-named **Sentinel** below (rename freely) — that happens to live in this same repo, in its own top-level directory, as its own independent service:

- Its own FastAPI process, listening on its own port (e.g. `8001`), with its own `systemd` unit.
- Its own login/session, own SQLite database file, own privileged-helper script + sudoers entry.
- **No changes to any existing file**: `backend/main.py`, `backend/auth.py`, `backend/collector.py`, `backend/models.py`, `backend/database.py`, and all of `frontend/` are untouched. No nav link is added to the existing dashboard, no shared session/cookie, no shared cache.

It reuses *ideas* from daedalus's existing conventions where they're just good patterns for this kind of project (the sudo-restricted privileged-helper script, dark-theme card/badge UI, PAM-based login) — but as independent code, copied and adapted, not imported or wired in.

### Existing daedalus app, for reference/context only

The current dashboard (`backend/` + `frontend/` at repo root) provides system monitoring (CPU/memory/disk/process stats), RAID array status, systemd services status, nginx status, a Sites Manager for nginx-hosted subdomains, a web terminal, and PAM-based login. Sentinel does not touch or depend on any of it.

## 2. Scope

Two capabilities, scoped for a personal home server (not an enterprise SIEM — no packet capture, no ML anomaly detection, no cross-host correlation):

1. **Malware/virus scanning** of the filesystem (hosted sites, home directories, or any path the owner points it at).
2. **Intrusion detection** — surfacing attempts by unauthorized parties to access the server (SSH brute force, banned IPs, unexpected open ports).
3. **Invader logging** — persist every intrusion attempt with the source IP address and its geolocation (country/city), so past attackers can be reviewed later, not just the live "recent events" feed.
4. **Vulnerability scanning** — periodic/on-demand check of the server's own open services against known CVEs, so misconfigurations/outdated services are caught before an attacker finds them.

## 3. Tool Choices & Rationale

- **Malware scanning: ClamAV** (`clamdscan` against the `clamd` daemon when running, falling back to one-shot `clamscan`). Standard mature open-source AV engine with a CLI and signature-update pipeline (`freshclam`) — shell out to it, no Python bindings needed. Rejected alternatives: `rkhunter`/`chkrootkit` (rootkit-only, no real signature DB), commercial engines (licensing/cloud dependency, overkill for a home server).
- **Intrusion detection: log parsing + optional fail2ban + connection snapshot**, not a SIEM:
  1. Parse SSH auth logs (`journalctl -u ssh`/`sshd`, falling back to `/var/log/auth.log`) for `Failed password`/`Invalid user` lines, aggregate failures per source IP to flag brute-force attempts.
  2. If `fail2ban` is installed, mirror its ban state via `fail2ban-client status <jail>` (read-only surfacing, not reimplementing its ban logic).
  3. Periodic snapshot of listening sockets (`psutil.net_connections`) to flag unexpected listeners.
- **IP geolocation: local MaxMind GeoLite2-City database** (`.mmdb` file, looked up via the `geoip2` Python library). Chosen over a live third-party geolocation API because lookups happen entirely offline — no per-lookup network call, no rate limits, and attacker IPs are never sent to an external service. The `.mmdb` file is downloaded once (free MaxMind account + license key) during setup and can be refreshed periodically; a missing/stale database degrades gracefully (events are still logged, just without country/city filled in).
- **Vulnerability scanning: `nmap` with vulnerability-detection scripts (`--script vuln`/`vulners`), not Metasploit.** Metasploit was considered — it was ruled out as an *automated, bundled* component for two reasons: (1) it's a full Ruby-based exploitation framework (hundreds of MB, its own module/database pipeline), a mismatch with every other tool choice here (ClamAV, fail2ban, nmap), which are all small, focused CLIs; (2) Sentinel is a network-facing automated service, and installing a full exploitation framework on the very host it defends — wired to run automatically — means a bug in Sentinel would hand an attacker a ready pentesting toolkit already on the box. nmap's vuln scripts do read-only version/CVE fingerprinting (no exploitation), which is a much smaller blast radius and matches the actual question being asked ("am I vulnerable," not "can this exploit succeed"). **Manual, one-off penetration testing with Metasploit against your own infrastructure remains something you can do yourself, entirely separate from Sentinel** — it's just not something this app automates or bundles.

## 4. Project Layout

New top-level directory, sibling to the existing `backend/`/`frontend`/`scripts`:

```
sentinel/
├── backend/
│   ├── __init__.py
│   ├── main.py            # its own FastAPI app + lifespan, own port (e.g. 8001)
│   ├── auth.py             # own login/session (PAM or a simple local password — decide before Phase 1)
│   ├── models.py           # Pydantic models (below)
│   ├── database.py         # own aiosqlite file: sentinel.db
│   ├── scanner.py          # malware scan logic
│   ├── intrusion.py        # log parsing / fail2ban / listener snapshot logic
│   └── vuln_scanner.py     # nmap-based CVE/vulnerability scanning logic
├── frontend/
│   ├── login.html
│   ├── index.html          # single dashboard page (scan + intrusion cards)
│   ├── app.js
│   └── style.css           # own stylesheet (can echo daedalus's dark palette, but not shared)
├── scripts/
│   ├── sentinel-helper.sh  # privileged root helper (own sudoers entry)
│   └── setup-sentinel.sh   # installs ClamAV/fail2ban, the helper, sudoers, systemd unit
└── requirements.txt         # own Python deps (fastapi, uvicorn, aiosqlite, psutil, geoip2, python-multipart, itsdangerous, + python-pam if PAM auth is reused)
```

Running this as its own `systemd` service on its own port keeps it operable even if the daedalus dashboard is down, and keeps a compromise of one app from automatically exposing the other.

## 5. Auth

Own session, independent of daedalus's cookie:

- Simplest option: reuse the PAM-against-Linux-accounts approach (`python-pam`, same idea as daedalus's `auth.py` but a separate `SECRET_KEY`/cookie name so sessions never cross apps).
- `require_auth(request)` dependency, same shape as daedalus's (redirect to `/login` if no session) — copied and adapted, not imported cross-directory.

## 6. Data Model (`sentinel/backend/models.py`)

```python
class ScanRequest(BaseModel):
    path: str | None = None   # defaults to a configured root if omitted

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
    country: str | None = None   # geolocated from source_ip, None if private/unresolvable
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None

class InvaderSummary(BaseModel):
    ip: str
    country: str | None
    city: str | None
    latitude: float | None = None
    longitude: float | None = None
    first_seen: str
    last_seen: str
    attempt_count: int
    event_types: list[str]        # distinct event_type values seen for this IP

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

class VulnFinding(BaseModel):
    id: int | None = None
    detected_at: str
    host: str
    port: int
    service: str | None = None
    cve_id: str | None = None
    severity: str                # "low" | "medium" | "high" | "critical"
    description: str

class VulnScanSummary(BaseModel):
    last_scan_at: str | None
    scan_running: bool
    findings_count: int
```

## 7. Persistence (`sentinel/backend/database.py`)

Own SQLite file (`sentinel.db`, separate from daedalus's unused `webmonitor.db`), with an `init_db()` bootstrap called at startup:

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
    severity TEXT NOT NULL,
    country TEXT,
    city TEXT,
    latitude REAL,
    longitude REAL
);

CREATE INDEX IF NOT EXISTS idx_intrusion_ip_time ON intrusion_events(source_ip, detected_at);

CREATE TABLE IF NOT EXISTS vuln_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    service TEXT,
    cve_id TEXT,
    severity TEXT NOT NULL,
    description TEXT NOT NULL
);
```

## 8. Backend Logic

**`sentinel/backend/scanner.py`**:
- `SCAN_ALLOWLIST` — configurable list of paths the app is allowed to scan (e.g. `/var/www`, `/home`); reject anything outside it.
- `async def trigger_scan(path) -> ScanResult` — validate path is allowlisted, reject if a scan is already running, insert a `status="running"` row, spawn `_run_scan()` as a background task, return immediately so the UI can poll.
- `async def _run_scan(path, row_id)` — run `clamdscan -r <path>` (or `clamscan` fallback) via `asyncio.create_subprocess_exec`, parse the scan summary + infected-file list, update the DB row.
- `get_scan_status()` / `list_scan_history()` — read accessors.

**`sentinel/backend/intrusion.py`**:
- `async def startup()` — probe `clamav_available`/`fail2ban_available` via `shutil.which`, run one initial refresh.
- `async def run_forever()` — periodic loop (e.g. every 45s) refreshing the intrusion snapshot, wrapped in try/except so one failure doesn't kill the loop.
- `_parse_auth_log()` — tail SSH auth logs, aggregate failures per IP, flag brute force.
- `_refresh_fail2ban()` — mirror `fail2ban-client status <jail>` if available.
- `_refresh_listeners()` — snapshot listening sockets via `psutil.net_connections`, flag unexpected ports.
- `_geolocate_ip(ip) -> tuple[country, city, lat, lon]` — looks up `ip` in the local GeoLite2-City `.mmdb` via `geoip2.database.Reader`; returns all-`None` for private/reserved IPs (`ipaddress.ip_address(ip).is_private`) or if the database file is missing, so a single bad/missing lookup never blocks logging the event itself.
- Every inserted `IntrusionEvent` row is enriched with `_geolocate_ip(source_ip)` before being written, so the log is queryable by location later even though only new alerts (post-dedupe) get written.
- Dedupe repeated alerts per IP+event_type within a time window (e.g. 10 minutes) so the loop doesn't flood the events table.
- `async def list_invaders() -> list[InvaderSummary]` — `SELECT source_ip, country, city, latitude, longitude, MIN(detected_at), MAX(detected_at), COUNT(*), GROUP_CONCAT(DISTINCT event_type) ... GROUP BY source_ip ORDER BY MAX(detected_at) DESC` against `intrusion_events` — no new table needed, this is just an aggregated read over the existing log.
- `ban_ip(ip)` / `unban_ip(ip)` — validate IP format (`ipaddress.ip_address`), call the privileged helper. Phase 3 only.

**Note:** start scanning/log-reading unprivileged first — likely readable without root depending on the service account's group membership. Only route an operation through the sudo helper if it actually hits a `PermissionError` in practice.

**`sentinel/backend/vuln_scanner.py`**:
- `async def trigger_vuln_scan(target="127.0.0.1") -> VulnScanSummary` — reject if a vuln scan is already running (same one-at-a-time guard as `scanner.py`), insert a `scan_running` marker, spawn `_run_vuln_scan()` as a background task, return immediately.
- `async def _run_vuln_scan(target, row_marker)` — run `nmap -sV --script vulners -oX -` via `asyncio.create_subprocess_exec`, capturing XML on stdout.
- `_parse_nmap_xml(xml_text) -> list[VulnFinding]` — parse with `xml.etree.ElementTree`, walk `<port>` elements, pull `<script id="vulners">` output for CVE IDs + CVSS-derived severity, fall back to service/version banner (`<service name= product= version=>`) with `severity="low"` and no `cve_id` when the vulners script isn't available/didn't match anything — so a scan without CVE matches still records what was found, not nothing.
- `get_vuln_status()` / `list_vulnerabilities()` — read accessors for the API routes.
- Only scans the box itself (`target` defaults to `127.0.0.1`); no automated scanning of other hosts on the network, keeping it self-assessment only.

## 9. API Routes (`sentinel/backend/main.py`)

Own FastAPI app, its own root-level paths (no need for a `/security` prefix since this is the whole app, not a section of a larger one):

| Method | Path | Purpose | Phase |
|---|---|---|---|
| GET | `/` | Dashboard page (scan + intrusion cards) | 1 |
| GET | `/login` | Login page | 1 |
| GET | `/api/status` | Full `SecurityStatus` snapshot | 1 (scan fields), 2/3 (rest) |
| POST | `/api/scan` | Trigger a scan (`ScanRequest` body), 202 + scan id, 409 if already running | 1 |
| GET | `/api/scan/{scan_id}` | Poll one scan's `ScanResult` | 1 |
| GET | `/api/scans` | Scan history (last 20) | 1 |
| GET | `/api/events` | Recent `IntrusionEvent`s (with location fields) | 2 |
| GET | `/api/invaders` | Aggregated per-IP `InvaderSummary` list (location, first/last seen, attempt count) | 2 |
| GET | `/api/banned` | Currently banned IPs | 3 |
| POST | `/api/ban` | Ban an IP (`{ip: str}`) | 3 |
| POST | `/api/unban` | Unban an IP | 3 |
| POST | `/api/vuln-scan` | Trigger a vulnerability scan of the local host, 409 if already running | 4 |
| GET | `/api/vulnerabilities` | List `VulnFinding` results from the most recent scan | 4 |

## 10. Privileged Helper (`sentinel/scripts/sentinel-helper.sh`)

Root-owned bash script, `set -euo pipefail`, `case "$CMD" in ...)` dispatch, validate-then-act (mirrors the *pattern* used by daedalus's `scripts/site-helper.sh`, as an independent script):

- `scan <path>` — `clamdscan -r <path>` as root, only for paths a `validate_path()` guard confirms (via `realpath`) are inside the allowlist — rejects traversal.
- `read-auth-log` — `cat` the auth log, only needed if the service account can't already read it.
- `fail2ban-status <jail>` — `fail2ban-client status <jail>`.
- `ban-ip <ip> [jail]` / `unban-ip <ip> [jail]` — `fail2ban-client set <jail> banip/unbanip <ip>`, with a `validate_ip()` regex guard first.

Installed with its own narrow `/etc/sudoers.d/sentinel` entry, NOPASSWD scoped to the exact helper path only.

## 11. Setup/Install Script (`sentinel/scripts/setup-sentinel.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail
# 1. apt-get install -y clamav clamav-daemon fail2ban
# 2. systemctl enable --now clamav-freshclam clamav-daemon fail2ban
# 3. freshclam                                    # initial signature pull
# 4. install -m 0755 sentinel/scripts/sentinel-helper.sh /usr/local/bin/sentinel-helper
# 5. write + validate /etc/sudoers.d/sentinel (visudo -c)
# 6. usermod -aG adm <sentinel-service-user>       # unprivileged auth-log reads where possible
# 7. download GeoLite2-City.mmdb (requires a free MaxMind account + license key) to
#    sentinel/backend/GeoLite2-City.mmdb — documented manual step, not automated (license key is a secret)
# 8. apt-get install -y nmap; nmap --script-updatedb   # ensure the vulners NSE script is present
# 9. install a systemd unit running `uvicorn sentinel.backend.main:app --port 8001`
```

Idempotent and safe to re-run.

## 12. Frontend (`sentinel/frontend/`)

Own dark-theme dashboard page (own `style.css`, not shared with daedalus's), with:

- **Scan card** — path selector, "Scan Now" button, last scan result badge (green if clean, red if threats found), scan history list.
- **Intrusion events card** — recent events table (time, IP, type, severity badge), auto-refreshed.
- **Invaders card** — one row per distinct attacker IP: country/city, first seen, last seen, attempt count, event types — the persistent "who's been trying to get in" log, separate from the live recent-events feed.
- **Banned IPs card** (Phase 3) — list with unban action, manual ban-IP input.
- **Unexpected listeners card** — table of non-allowlisted open ports.
- **Vulnerabilities card** (Phase 4) — host/port/service/CVE/severity table, "Scan Now" button, last-scan timestamp.
- Own `esc()`-style HTML-escaping helper for rendering file paths/log lines/IPs, since that data can contain attacker-influenced content and must never be interpolated as raw HTML.

## 13. Phasing

- **Phase 1 — Scaffolding + malware scan (on-demand)**: project skeleton (`sentinel/backend`, `sentinel/frontend`, own `requirements.txt`), own login/auth, `scan_results` table, scanner logic, scan routes, scan-card UI. Skip the privileged helper initially — add it only if a real permission wall is hit.
- **Phase 2 — Intrusion log parsing + alerting**: `intrusion_events` table (with location columns), remaining models (`IntrusionEvent`, `InvaderSummary`, `NetworkListener`), auth-log parsing/listener-snapshot logic, GeoLite2 lookup wiring, `/api/invaders`, background task wiring, events + invaders + listeners UI.
- **Phase 3 — fail2ban integration + IP banning**: `BannedIP` model, fail2ban mirroring, helper `ban-ip`/`unban-ip`/`fail2ban-status` subcommands + sudoers, ban/unban routes and UI.
- **Phase 4 — Vulnerability scanning**: `VulnFinding`/`VulnScanSummary` models, `vuln_findings` table, `vuln_scanner.py` (nmap + `vulners` NSE script), `/api/vuln-scan` + `/api/vulnerabilities` routes, Vulnerabilities UI card, `nmap` added to `setup-sentinel.sh`. Independent of Phases 1-3 — can be built any time after Phase 1's scaffolding exists.

This order front-loads the lowest-risk, highest-value capability (scanning known file locations) before adding privileged network-defense actions.

## 14. Possible Future Integration (not now)

Once Sentinel exists as a working standalone service, it could optionally be exposed as its own subdomain through daedalus's existing Sites Manager (`/sites`), or linked from the dashboard nav — but that's a later decision, not part of this plan, and would still not require merging the two codebases or sharing sessions/databases.

## Out of Scope (for this document)

This is a design document only — no code, dependencies, or other files are changed as part of writing this plan.
