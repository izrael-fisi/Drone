"""Lightweight hardware probes shared by saved-image and live-runtime measurements."""

import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

try:
    import resource
except ImportError:
    resource = None


def _process_rss_bytes(status: Path = Path("/proc/self/status")) -> int | None:
    if status.is_file():
        try:
            for line in status.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) != 3 or parts[2] != "kB" or int(parts[1]) < 0:
                        return None
                    return int(parts[1]) * 1024
        except (OSError, IndexError, ValueError):
            return None
        return None
    try:
        if resource is None:
            return None
        maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return maximum if sys.platform == "darwin" else maximum * 1024
    except (ValueError, OSError):
        return None


def _thermal_snapshot(root: Path = Path("/sys/class/thermal")) -> dict:
    samples = []
    for path in sorted(root.glob("thermal_zone*/temp")):
        try:
            raw = float(path.read_text().strip())
            # Linux thermal-zone sysfs temperatures are always millidegrees C.
            temperature = raw / 1000.0
            if not math.isfinite(temperature):
                continue
            type_path = path.parent / "type"
            samples.append({"zone": path.parent.name,
                "type": type_path.read_text().strip() if type_path.is_file() else None,
                "temperature_c": round(temperature, 3)})
        except (OSError, ValueError):
            continue
    return {"status": "available" if samples else "unavailable", "samples": samples,
            "max_temperature_c": max((row["temperature_c"] for row in samples), default=None)}


def hardware_identity() -> dict:
    model = None
    for path in (Path("/sys/firmware/devicetree/base/model"), Path("/proc/device-tree/model")):
        try:
            model = path.read_text().strip("\x00\n ")
            break
        except OSError:
            continue
    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "python": platform.python_version(), "logical_cpu_count": os.cpu_count(), "board_model": model,
            "is_raspberry_pi": bool(model and model.startswith("Raspberry Pi")),
            "rss_measurement": "current_proc_vmrss" if Path("/proc/self/status").is_file()
                else "process_lifetime_peak_ru_maxrss" if resource is not None else "unavailable"}


def parse_throttled(text: str) -> dict:
    match = re.fullmatch(r"throttled=(0x[0-9a-fA-F]+)\s*", text.strip())
    if not match:
        raise ValueError("Malformed vcgencmd get_throttled response")
    value = int(match[1], 16)
    if value & ~0xF000F:
        raise ValueError("Unknown Raspberry Pi throttle bits; do not assume healthy")
    names = ("undervoltage", "frequency_capped", "throttled", "soft_temperature_limit")
    return {"status": "available", "raw": hex(value),
            "current": [name for bit, name in enumerate(names) if value & (1 << bit)],
            "since_boot": [name for bit, name in enumerate(names) if value & (1 << (bit + 16))]}


def resource_snapshot(is_pi: bool) -> dict:
    thermal = _thermal_snapshot()
    throttle = {"status": "unavailable", "current": [], "since_boot": []}
    command = shutil.which("vcgencmd") if is_pi else None
    if command:
        try:
            result = subprocess.run([command, "get_throttled"], capture_output=True, text=True, check=True, timeout=1)
            throttle = parse_throttled(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return {"rss_bytes": _process_rss_bytes(), "max_temperature_c": thermal["max_temperature_c"], "throttle": throttle}


def process_io_counters(path: Path = Path("/proc/self/io")) -> dict:
    """Kernel-accounted storage demand, not physical microSD wear or flash writes."""
    keys = ("read_bytes", "write_bytes", "cancelled_write_bytes")
    try:
        values = {}
        for line in path.read_text().splitlines():
            key, separator, value = line.partition(":")
            if separator and key in keys:
                parsed = int(value.strip())
                if parsed < 0 or key in values:
                    raise ValueError("Invalid or duplicate process I/O counter")
                values[key] = parsed
        if set(values) != set(keys):
            raise ValueError("Incomplete process I/O counters")
        return {"status": "available", **values}
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": f"{type(exc).__name__}:{exc}"[:500]}
