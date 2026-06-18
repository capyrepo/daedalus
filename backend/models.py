from pydantic import BaseModel


class DiskInfo(BaseModel):
    path: str
    device: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class SystemMetrics(BaseModel):
    cpu_percent: float
    memory_percent: float
    memory_used_gb: float
    memory_total_gb: float
    disks: list[DiskInfo]
    net_sent_kbps: float
    net_recv_kbps: float


class NginxStatus(BaseModel):
    running: bool
    active_connections: int
    requests_per_sec: float


class RaidDevice(BaseModel):
    name: str
    up: bool


class RaidSync(BaseModel):
    operation: str   # resync / rebuild / check / repair
    percent: float
    finish_min: float
    speed_kbps: int


class RaidArray(BaseModel):
    name: str          # e.g. md0
    state: str         # active / inactive / degraded
    level: str         # e.g. RAID 1
    devices_total: int
    devices_active: int
    devices: list[RaidDevice]
    sync: RaidSync | None


class ProcessInfo(BaseModel):
    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    status: str


class ServiceInfo(BaseModel):
    name: str
    load: str
    active: str
    sub: str
    description: str

