"""Raspberry Pi host metrics from read-only Docker host mounts."""

import os
import time
from pathlib import Path

PROC = Path("/host/proc")
THERMAL = Path("/host/thermal/thermal_zone0/temp")
ROOT = Path("/host/root")


class HostSystemMetrics:
    def __init__(self):
        self.started = time.monotonic()
        self.previous_cpu = None

    @staticmethod
    def empty():
        return dict.fromkeys((
            "cpu_temp_c", "cpu_usage_pct", "memory_usage_pct",
            "disk_usage_pct", "host_uptime_seconds", "container_uptime_seconds",
        ))

    def collect(self):
        result = self.empty()
        result["container_uptime_seconds"] = int(time.monotonic() - self.started)
        try:
            result["cpu_temp_c"] = round(int(THERMAL.read_text().strip()) / 1000, 2)
        except (OSError, ValueError):
            pass
        try:
            result["host_uptime_seconds"] = int(float((PROC / "uptime").read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            pass
        try:
            fields = (PROC / "stat").read_text().splitlines()[0].split()
            if fields[0] != "cpu":
                raise ValueError("Missing CPU counters")
            counters = [int(x) for x in fields[1:]]
            total = sum(counters)
            idle = counters[3] + counters[4]
            if self.previous_cpu is not None:
                old_total, old_idle = self.previous_cpu
                delta = total - old_total
                if delta > 0:
                    result["cpu_usage_pct"] = round(max(0, min(100, 100 * (1 - (idle - old_idle) / delta))), 2)
            self.previous_cpu = total, idle
        except (OSError, ValueError, IndexError):
            pass
        try:
            memory = {}
            for line in (PROC / "meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                memory[key] = int(value.strip().split()[0])
            total = memory["MemTotal"]
            if total > 0:
                result["memory_usage_pct"] = round(100 * (total - memory["MemAvailable"]) / total, 2)
        except (OSError, ValueError, IndexError, KeyError):
            pass
        try:
            disk = os.statvfs(ROOT)
            if disk.f_blocks > 0:
                result["disk_usage_pct"] = round(100 * (disk.f_blocks - disk.f_bavail) / disk.f_blocks, 2)
        except OSError:
            pass
        return result
