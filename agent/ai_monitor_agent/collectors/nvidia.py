"""NVIDIA GPU collector backed by NVML (nvidia-ml-py)."""

from __future__ import annotations

import logging

from .base import DeviceSample, ProcessMem

log = logging.getLogger(__name__)


class NvidiaCollector:
    name = "nvidia"

    def __init__(self) -> None:
        self._nvml = None
        self._initialised = False
        self._warned = False

    def _warn_once(self, msg: str, *args) -> None:
        if not self._warned:
            log.warning(msg, *args)
            self._warned = True

    def available(self) -> bool:
        if self._initialised:
            return True
        try:
            import pynvml  # type: ignore
        except ImportError as exc:
            self._warn_once("pynvml not importable (%s); NVIDIA collector disabled", exc)
            return False
        try:
            pynvml.nvmlInit()
        except Exception as exc:  # NVMLError or anything else from a broken driver
            self._warn_once("nvmlInit failed (%s); NVIDIA collector disabled", exc)
            return False
        self._nvml = pynvml
        self._initialised = True
        return True

    def _ensure(self):
        if not self._initialised and not self.available():
            raise RuntimeError("NVML not available")
        return self._nvml

    def collect(self) -> list[DeviceSample]:
        nv = self._ensure()
        samples: list[DeviceSample] = []
        for index in range(nv.nvmlDeviceGetCount()):
            handle = nv.nvmlDeviceGetHandleByIndex(index)
            name = nv.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            util = nv.nvmlDeviceGetUtilizationRates(handle)
            mem = nv.nvmlDeviceGetMemoryInfo(handle)
            temp = nv.nvmlDeviceGetTemperature(handle, nv.NVML_TEMPERATURE_GPU)
            power_mw = nv.nvmlDeviceGetPowerUsage(handle)

            ecc: int | None
            try:
                ecc = int(
                    nv.nvmlDeviceGetTotalEccErrors(
                        handle, nv.NVML_MEMORY_ERROR_TYPE_UNCORRECTED, nv.NVML_VOLATILE_ECC
                    )
                )
            except Exception:
                ecc = None

            processes: list[ProcessMem] = []
            try:
                for p in nv.nvmlDeviceGetComputeRunningProcesses(handle):
                    used = p.usedGpuMemory
                    processes.append(ProcessMem(pid=int(p.pid), mem_bytes=int(used) if used else 0))
            except Exception as exc:
                log.debug("process list unavailable for gpu %d: %s", index, exc)

            samples.append(
                DeviceSample(
                    vendor="nvidia",
                    index=index,
                    model=str(name),
                    util_percent=float(util.gpu),
                    mem_used_bytes=int(mem.used),
                    mem_total_bytes=int(mem.total),
                    temp_celsius=float(temp),
                    power_watts=float(power_mw) / 1000.0,
                    ecc_errors_total=ecc,
                    health=0,
                    processes=processes,
                )
            )
        return samples
