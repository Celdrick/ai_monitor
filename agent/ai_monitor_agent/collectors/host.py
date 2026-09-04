"""Host-level metrics via psutil."""

from __future__ import annotations

import logging

import psutil

from .base import HostSample

log = logging.getLogger(__name__)


class HostCollector:
    name = "host"

    def __init__(self) -> None:
        # Prime the CPU counter so the first real reading is meaningful.
        psutil.cpu_percent(interval=None)

    def available(self) -> bool:
        return True

    def collect(self) -> HostSample:
        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()

        disks: dict[str, tuple[int, int]] = {}
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError) as exc:
                log.debug("skip disk %s: %s", part.mountpoint, exc)
                continue
            disks[part.mountpoint] = (usage.used, usage.total)

        net: dict[str, tuple[int, int]] = {}
        for iface, counters in psutil.net_io_counters(pernic=True).items():
            if iface == "lo":
                continue
            net[iface] = (counters.bytes_recv, counters.bytes_sent)

        return HostSample(
            cpu_percent=float(cpu),
            mem_used_bytes=int(vm.total - vm.available),
            mem_total_bytes=int(vm.total),
            disks=disks,
            net=net,
        )
