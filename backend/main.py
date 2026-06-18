import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import collector
from .auth import require_auth, verify_system_credentials
from .models import NginxStatus, ProcessInfo, RaidArray, ServiceInfo, SystemMetrics
from .terminal import terminal_endpoint

SECRET_KEY = os.environ.get("SECRET_KEY", "changeme-set-SECRET_KEY-env-var")
if SECRET_KEY == "changeme-set-SECRET_KEY-env-var":
    print("WARNING: using default SECRET_KEY — set the SECRET_KEY env var for production")

_LOGIN_HTML: str = ""
_INDEX_HTML: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _LOGIN_HTML, _INDEX_HTML
    _LOGIN_HTML = Path("frontend/login.html").read_text()
    _INDEX_HTML = Path("frontend/index.html").read_text()
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


# --- Protected routes ---

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


# --- WebSocket terminal ---

@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    await terminal_endpoint(websocket)


app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=302)
    return _INDEX_HTML
