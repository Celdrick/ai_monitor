"""NVIDIA collector tests using a fake pynvml module (no GPU required)."""

import sys
import types

import pytest

from ai_monitor_agent.collectors.nvidia import NvidiaCollector


class _Mem:
    used = 10 * 1024**3
    total = 80 * 1024**3


class _Util:
    gpu = 55
    memory = 40


class _Proc:
    def __init__(self, pid, used):
        self.pid = pid
        self.usedGpuMemory = used


def _make_fake_pynvml(*, init_fails=False, ecc_supported=True):
    mod = types.ModuleType("pynvml")

    class NVMLError(Exception):
        pass

    class NVMLError_NotSupported(NVMLError):
        pass

    mod.NVMLError = NVMLError
    mod.NVMLError_NotSupported = NVMLError_NotSupported
    mod.NVML_TEMPERATURE_GPU = 0
    mod.NVML_VOLATILE_ECC = 0
    mod.NVML_MEMORY_ERROR_TYPE_UNCORRECTED = 1
    mod.NVML_MEMORY_ERROR_TYPE_CORRECTED = 0

    def nvmlInit():
        if init_fails:
            raise NVMLError("driver not loaded")

    mod.nvmlInit = nvmlInit
    mod.nvmlShutdown = lambda: None
    mod.nvmlDeviceGetCount = lambda: 2
    mod.nvmlDeviceGetHandleByIndex = lambda i: ("handle", i)
    mod.nvmlDeviceGetName = lambda h: f"NVIDIA A100 {h[1]}"
    mod.nvmlDeviceGetUtilizationRates = lambda h: _Util()
    mod.nvmlDeviceGetMemoryInfo = lambda h: _Mem()
    mod.nvmlDeviceGetTemperature = lambda h, s: 61
    mod.nvmlDeviceGetPowerUsage = lambda h: 250_000  # milliwatts

    def nvmlDeviceGetTotalEccErrors(h, err_type, counter):
        if not ecc_supported:
            raise NVMLError_NotSupported()
        return 3 if err_type == mod.NVML_MEMORY_ERROR_TYPE_UNCORRECTED else 0

    mod.nvmlDeviceGetTotalEccErrors = nvmlDeviceGetTotalEccErrors
    mod.nvmlDeviceGetComputeRunningProcesses = lambda h: [
        _Proc(1000 + h[1], 2 * 1024**3),
        _Proc(2000 + h[1], None),
    ]
    return mod


@pytest.fixture
def fake_pynvml(monkeypatch):
    def _install(**kwargs):
        mod = _make_fake_pynvml(**kwargs)
        monkeypatch.setitem(sys.modules, "pynvml", mod)
        return mod

    return _install


def test_available_and_collect(fake_pynvml):
    fake_pynvml()
    c = NvidiaCollector()
    assert c.name == "nvidia"
    assert c.available() is True
    samples = c.collect()
    assert len(samples) == 2
    s = samples[0]
    assert s.vendor == "nvidia"
    assert s.index == 0
    assert s.model == "NVIDIA A100 0"
    assert s.util_percent == 55
    assert s.mem_used_bytes == 10 * 1024**3
    assert s.mem_total_bytes == 80 * 1024**3
    assert s.temp_celsius == 61
    assert s.power_watts == pytest.approx(250.0)
    assert s.ecc_errors_total == 3
    assert s.health == 0
    assert [p.pid for p in s.processes] == [1000, 2000]
    assert s.processes[0].mem_bytes == 2 * 1024**3
    assert s.processes[1].mem_bytes == 0


def test_ecc_not_supported_gives_none(fake_pynvml):
    fake_pynvml(ecc_supported=False)
    samples = NvidiaCollector().collect()
    assert all(s.ecc_errors_total is None for s in samples)


def test_available_false_when_init_fails(fake_pynvml, caplog):
    fake_pynvml(init_fails=True)
    c = NvidiaCollector()
    with caplog.at_level("WARNING"):
        assert c.available() is False
        assert c.available() is False
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


def test_available_false_when_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)  # makes `import pynvml` raise ImportError
    assert NvidiaCollector().available() is False
