from __future__ import annotations

import os
import shutil

from app.config import Settings
from app.domain.errors import ResourceLimit


def available_ram_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return 4096
    return 4096


def process_rss_mb() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return 0
    return 0


def free_disk_mb(path: str) -> int:
    usage = shutil.disk_usage(path)
    return usage.free // (1024 * 1024)


def guard_resources(settings: Settings, storage_root: str) -> None:
    if not settings.resource_guard_enabled:
        return
    if available_ram_mb() < settings.min_available_ram_mb:
        raise ResourceLimit("memori tersisa di bawah ambang aman")
    if free_disk_mb(storage_root) < settings.min_free_disk_mb:
        raise ResourceLimit("ruang disk di bawah ambang aman")


def cpu_percent() -> float:
    try:
        load = os.getloadavg()[0]
        return round(load * 25.0, 1)
    except OSError:
        return 0.0
