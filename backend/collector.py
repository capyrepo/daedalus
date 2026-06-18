import asyncio
import re
import time
from typing import Any

import httpx
import psutil

from .models import DiskInfo, NginxStatus, ProcessInfo, ServiceInfo, SystemMetrics
from .raid import _parse_mdstat

_cache: dict[str, Any] = {}
_http_client: httpx.AsyncClient | None = None

_last_net_time: float = 0.0
_last_net_sent: int = 0
_last_net_recv: int = 0

_last_requests: int = 0
_last_requests_time: float = 0.0

_raid_last_collected: float = 0.0
_services_last_collected: float = 0.0

SYSTEM_INTERVAL = 5.0
RAID_INTERVAL = 10.0
SERVICES_INTERVAL = 15.0


async def startup() -> None:
    global _http_client
    _http_client = httpx.AsyncClient(timeout=2.0)
    await _refresh_system()
    await _refresh_nginx()
    await _refresh_processes()
    await _refresh_raid(force=True)
    await _refresh_services(force=True)


async def shutdown() -> None:
    if _http_client:
        await _http_client.aclose()


async def run_forever() -> None:
    while True:
        try:
            await _refresh_system()
            await _refresh_nginx()
            await _refresh_processes()
            await _refresh_raid()
            await _refresh_services()
        except Exception:
            pass
        await asyncio.sleep(SYSTEM_INTERVAL)


def get_cached(key: str) -> Any:
    return _cache.get(key)


async def _refresh_system() -> None:
    global _last_net_time, _last_net_sent, _last_net_recv

    net = psutil.net_io_counters()
    now = time.monotonic()
    elapsed = now - _last_net_time if _last_net_time else 1.0
    sent_kbps = ((net.bytes_sent - _last_net_sent) / elapsed / 1024) if _last_net_time else 0.0
    recv_kbps = ((net.bytes_recv - _last_net_recv) / elapsed / 1024) if _last_net_time else 0.0
    _last_net_time = now
    _last_net_sent = net.bytes_sent
    _last_net_recv = net.bytes_recv

    mem = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint.startswith("/snap/") or part.fstype == "squashfs":
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(DiskInfo(
                path=part.mountpoint,
                device=part.device,
                total_gb=round(usage.total / 1024**3, 1),
                used_gb=round(usage.used / 1024**3, 1),
                free_gb=round(usage.free / 1024**3, 1),
                percent=usage.percent,
            ))
        except PermissionError:
            pass

    _cache["system"] = SystemMetrics(
        cpu_percent=psutil.cpu_percent(interval=None),
        memory_percent=mem.percent,
        memory_used_gb=round(mem.used / 1024**3, 1),
        memory_total_gb=round(mem.total / 1024**3, 1),
        disks=disks,
        net_sent_kbps=round(max(sent_kbps, 0.0), 2),
        net_recv_kbps=round(max(recv_kbps, 0.0), 2),
    )


async def _refresh_nginx() -> None:
    global _last_requests, _last_requests_time

    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "nginx",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        running = stdout.decode().strip() == "active"
    except Exception:
        running = False

    active_connections = 0
    requests_per_sec = 0.0
    try:
        resp = await _http_client.get("http://127.0.0.1/nginx_status")
        text = resp.text
        m = re.search(r"Active connections:\s+(\d+)", text)
        if m:
            active_connections = int(m.group(1))
        m = re.search(r"\s+(\d+)\s+(\d+)\s+(\d+)", text)
        if m:
            total_requests = int(m.group(3))
            now = time.monotonic()
            if _last_requests_time:
                elapsed = now - _last_requests_time
                requests_per_sec = round(max((total_requests - _last_requests) / elapsed, 0.0), 2)
            _last_requests = total_requests
            _last_requests_time = now
    except Exception:
        pass

    _cache["nginx"] = NginxStatus(
        running=running,
        active_connections=active_connections,
        requests_per_sec=requests_per_sec,
    )


async def _refresh_processes() -> None:
    def _scan() -> list[ProcessInfo]:
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
            try:
                info = p.info
                procs.append(ProcessInfo(
                    pid=info['pid'],
                    name=info['name'] or '',
                    cpu_percent=round(info['cpu_percent'] or 0.0, 1),
                    memory_percent=round(info['memory_percent'] or 0.0, 2),
                    status=info['status'] or '',
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda p: p.cpu_percent, reverse=True)
        return procs[:10]

    _cache["processes"] = await asyncio.to_thread(_scan)


async def _refresh_raid(force: bool = False) -> None:
    global _raid_last_collected
    now = time.monotonic()
    if not force and now - _raid_last_collected < RAID_INTERVAL:
        return
    _raid_last_collected = now
    try:
        text = await asyncio.to_thread(lambda: open("/proc/mdstat").read())
        _cache["raid"] = _parse_mdstat(text)
    except OSError:
        _cache["raid"] = []


async def _refresh_services(force: bool = False) -> None:
    global _services_last_collected
    now = time.monotonic()
    if not force and now - _services_last_collected < SERVICES_INTERVAL:
        return
    _services_last_collected = now
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "list-units", "--type=service", "--state=failed",
            "--no-pager", "--no-legend", "--plain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        services = []
        for line in stdout.decode().strip().splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 4:
                services.append(ServiceInfo(
                    name=parts[0],
                    load=parts[1],
                    active=parts[2],
                    sub=parts[3],
                    description=parts[4] if len(parts) > 4 else "",
                ))
        _cache["services"] = services
    except Exception:
        _cache.setdefault("services", [])
