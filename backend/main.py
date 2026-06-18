import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import collector
from .auth import require_auth, verify_system_credentials
from .models import NginxStatus, ProcessInfo, RaidArray, ServiceInfo, Site, SiteCreate, SystemMetrics
from .sites import create_site, delete_site, disable_site, enable_site, list_sites
from .terminal import terminal_endpoint

SECRET_KEY = os.environ.get("SECRET_KEY", "changeme-set-SECRET_KEY-env-var")
if SECRET_KEY == "changeme-set-SECRET_KEY-env-var":
    print("WARNING: using default SECRET_KEY — set the SECRET_KEY env var for production")

_LOGIN_HTML: str = ""
_INDEX_HTML: str = ""
_SITES_HTML: str = ""

_NAME_RE = re.compile(r"^[a-zA-Z0-9-]{1,63}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _LOGIN_HTML, _INDEX_HTML, _SITES_HTML
    _LOGIN_HTML = Path("frontend/login.html").read_text()
    _INDEX_HTML = Path("frontend/index.html").read_text()
    _SITES_HTML = Path("frontend/sites.html").read_text()
    await collector.startup()
    task = asyncio.create_task(collector.run_forever())
    yield
    task.cancel()
    await collector.shutdown()


app = FastAPI(title="Server Monitor", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False)


# --- Auth routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    error_html = '<div class="error">Invalid username or password.</div>' if error else ""
    return _LOGIN_HTML.replace("__ERROR__", error_html)


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if verify_system_credentials(username, password):
        request.session["username"] = username
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=1", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# --- Dashboard ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=302)
    return _INDEX_HTML


# --- Sites manager page ---

@app.get("/sites", response_class=HTMLResponse)
async def sites_page(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=302)
    return _SITES_HTML


# --- Monitoring API ---

@app.get("/api/system", response_model=SystemMetrics)
async def system_metrics(auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    return collector.get_cached("system")


@app.get("/api/nginx", response_model=NginxStatus)
async def nginx_status(auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    return collector.get_cached("nginx")


@app.get("/api/raid", response_model=list[RaidArray])
async def raid_status(auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    return collector.get_cached("raid")


@app.get("/api/processes", response_model=list[ProcessInfo])
async def process_list(auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    return collector.get_cached("processes")


@app.get("/api/services/failed", response_model=list[ServiceInfo])
async def failed_services(auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    return collector.get_cached("services")


# --- Sites API ---

@app.get("/api/sites", response_model=list[Site])
async def api_list_sites(auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    return list_sites()


@app.post("/api/sites", status_code=201)
async def api_create_site(data: SiteCreate, auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    if not _NAME_RE.match(data.name):
        raise HTTPException(400, "Invalid subdomain name — use only letters, numbers, hyphens (max 63 chars)")
    if data.type not in ("static", "python", "node"):
        raise HTTPException(400, "type must be static, python, or node")
    if data.type in ("python", "node") and not data.port:
        raise HTTPException(400, "port is required for python and node apps")
    if data.port and not (1024 <= data.port <= 65535):
        raise HTTPException(400, "port must be between 1024 and 65535")
    try:
        await create_site(data.name, data.type, data.port)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.delete("/api/sites/{name}")
async def api_delete_site(name: str, auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    if not _NAME_RE.match(name):
        raise HTTPException(400, "Invalid site name")
    try:
        await delete_site(name)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.post("/api/sites/{name}/enable")
async def api_enable_site(name: str, auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    if not _NAME_RE.match(name):
        raise HTTPException(400, "Invalid site name")
    try:
        await enable_site(name)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.post("/api/sites/{name}/disable")
async def api_disable_site(name: str, auth=Depends(require_auth)):
    if isinstance(auth, RedirectResponse):
        return auth
    if not _NAME_RE.match(name):
        raise HTTPException(400, "Invalid site name")
    try:
        await disable_site(name)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


# --- WebSocket terminal ---

@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    await terminal_endpoint(websocket)


app.mount("/static", StaticFiles(directory="frontend"), name="static")
