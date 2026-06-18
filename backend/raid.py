import re
from .models import RaidArray, RaidDevice, RaidSync

_LEVEL_NAMES = {"raid0": "RAID 0", "raid1": "RAID 1", "raid5": "RAID 5", "raid6": "RAID 6", "raid10": "RAID 10"}


def _parse_mdstat(text: str) -> list[RaidArray]:
    arrays = []
    # Each array entry starts with "mdN : "; grab it plus its indented continuation lines
    blocks = re.findall(r"^(md\w+\s+:.*(?:\n[ \t]+.*)*)", text, re.MULTILINE)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        # --- line 1: mdN : (active|inactive) [level] dev[n] dev[n] ... ---
        m = re.match(r"^(md\w+)\s+:\s+(\S+)\s+(\S+)\s+(.*)", lines[0])
        if not m:
            continue
        name, raw_state, raw_level, dev_str = m.groups()
        level = _LEVEL_NAMES.get(raw_level, raw_level.upper())

        dev_tokens = re.findall(r"(\w+)\[(\d+)\](\(F\))?", dev_str)
        devices = [RaidDevice(name=d, up=(f != "(F)")) for d, _, f in dev_tokens]

        # --- line 2: blocks + [n/n] [UU__] ---
        state = raw_state
        devices_total = len(devices)
        devices_active = len(devices)
        if len(lines) > 1:
            counts = re.search(r"\[(\d+)/(\d+)\]", lines[1])
            if counts:
                devices_total = int(counts.group(1))
                devices_active = int(counts.group(2))
            flags = re.search(r"\[([U_]+)\]", lines[1])
            if flags:
                flag_str = flags.group(1)
                devices = [RaidDevice(name=d.name, up=(flag_str[i] == "U") if i < len(flag_str) else d.up)
                           for i, d in enumerate(devices)]
                if "_" in flag_str:
                    state = "degraded"

        # --- optional sync line ---
        sync = None
        for line in lines[2:]:
            sm = re.search(
                r"(resync|rebuild|check|repair)\s*=\s*([\d.]+)%"
                r".*?finish=([\d.]+)min\s+speed=(\d+)K/sec",
                line,
            )
            if sm:
                sync = RaidSync(
                    operation=sm.group(1),
                    percent=float(sm.group(2)),
                    finish_min=float(sm.group(3)),
                    speed_kbps=int(sm.group(4)),
                )
                break

        arrays.append(RaidArray(
            name=name,
            state=state,
            level=level,
            devices_total=devices_total,
            devices_active=devices_active,
            devices=devices,
            sync=sync,
        ))

    return arrays


def get_raid_arrays() -> list[RaidArray]:
    try:
        return _parse_mdstat(open("/proc/mdstat").read())
    except OSError:
        return []
