"""Synthetic device collector for development without real hardware."""

from __future__ import annotations

import random

from .base import DeviceSample, ProcessMem

GIB = 1024**3

_VENDOR_PROFILES = {
    "nvidia": {"model": "NVIDIA A100-SXM4-80GB", "mem_total": 80 * GIB},
    "ascend": {"model": "Ascend 910B4", "mem_total": 64 * GIB},
}


class FakeCollector:
    def __init__(self, vendor: str, count: int, seed: int | None = None) -> None:
        if vendor not in _VENDOR_PROFILES:
            raise ValueError(f"unsupported fake vendor {vendor!r}; expected one of {sorted(_VENDOR_PROFILES)}")
        self.vendor = vendor
        self.count = count
        self.name = f"fake-{vendor}"
        self._rng = random.Random(seed)
        self._profile = _VENDOR_PROFILES[vendor]

    def available(self) -> bool:
        return True

    def collect(self) -> list[DeviceSample]:
        rng = self._rng
        mem_total = self._profile["mem_total"]
        samples: list[DeviceSample] = []
        for index in range(self.count):
            mem_used = rng.randint(0, mem_total)
            proc_mem = rng.randint(0, mem_used) if mem_used > 0 else 0
            samples.append(
                DeviceSample(
                    vendor=self.vendor,
                    index=index,
                    model=self._profile["model"],
                    util_percent=round(rng.uniform(0, 100), 1),
                    mem_used_bytes=mem_used,
                    mem_total_bytes=mem_total,
                    temp_celsius=round(rng.uniform(30, 80), 1),
                    power_watts=round(rng.uniform(50, 400), 1),
                    ecc_errors_total=rng.randint(0, 5) if self.vendor == "nvidia" else None,
                    health=0 if self.vendor == "nvidia" else rng.choices([0, 1, 2], weights=[90, 8, 2])[0],
                    processes=[ProcessMem(pid=10000 + index, mem_bytes=proc_mem)],
                )
            )
        return samples
