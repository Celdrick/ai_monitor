"""Shared sample types and the collector protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ProcessMem:
    pid: int
    mem_bytes: int


@dataclass
class DeviceSample:
    vendor: str
    index: int
    model: str
    util_percent: float
    mem_used_bytes: int
    mem_total_bytes: int
    temp_celsius: float
    power_watts: float
    ecc_errors_total: int | None = None  # NVIDIA only
    health: int = 0  # Ascend health code, 0 = OK
    processes: list[ProcessMem] = field(default_factory=list)


@dataclass
class HostSample:
    cpu_percent: float
    mem_used_bytes: int
    mem_total_bytes: int
    disks: dict[str, tuple[int, int]]  # mount -> (used, total)
    net: dict[str, tuple[int, int]]  # iface -> (rx_total, tx_total)


@runtime_checkable
class DeviceCollector(Protocol):
    name: str

    def available(self) -> bool: ...

    def collect(self) -> list[DeviceSample]: ...
