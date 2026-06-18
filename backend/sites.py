import asyncio
import re
from pathlib import Path

from .models import Site

NGINX_AVAILABLE = Path("/etc/nginx/sites-available")
NGINX_ENABLED = Path("/etc/nginx/sites-enabled")
DOMAIN = "ent3.tech"
HELPER = "/usr/local/bin/webmonitor-site-helper"


async def _run_helper(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "sudo", HELPER, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


def list_sites() -> list[Site]:
    if not NGINX_AVAILABLE.exists():
        return []
    sites = []
    for conf in sorted(NGINX_AVAILABLE.iterdir()):
        name = conf.name
        if name in ("default", "webmonitor") or name.startswith("."):
            continue
        if not name.endswith(f".{DOMAIN}"):
            continue
        try:
            content = conf.read_text()
        except OSError:
            continue
        site_type = "static"
        port = None
        m = re.search(r"#\s*webmonitor-type:\s*(\S+)", content)
        if m:
            site_type = m.group(1)
        m = re.search(r"#\s*webmonitor-port:\s*(\d+)", content)
        if m:
            port = int(m.group(1))
        enabled = (NGINX_ENABLED / name).is_symlink()
        sites.append(Site(subdomain=name, type=site_type, port=port, enabled=enabled))
    return sites


async def create_site(name: str, site_type: str, port: int | None) -> None:
    if site_type == "static":
        rc, _, err = await _run_helper("create", "static", name)
    else:
        rc, _, err = await _run_helper("create", site_type, name, str(port))
    if rc != 0:
        raise RuntimeError(err or "helper failed")


async def delete_site(name: str) -> None:
    rc, _, err = await _run_helper("delete", name)
    if rc != 0:
        raise RuntimeError(err or "helper failed")


async def enable_site(name: str) -> None:
    rc, _, err = await _run_helper("enable", name)
    if rc != 0:
        raise RuntimeError(err or "helper failed")


async def disable_site(name: str) -> None:
    rc, _, err = await _run_helper("disable", name)
    if rc != 0:
        raise RuntimeError(err or "helper failed")
