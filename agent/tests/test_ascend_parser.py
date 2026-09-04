import subprocess
from pathlib import Path

import pytest

from ai_monitor_agent.collectors.ascend import AscendCollector, parse_npu_smi_info
from ai_monitor_agent.collectors.base import ProcessMem

FIXTURES = Path(__file__).parent / "fixtures"
MB = 1024 * 1024


@pytest.fixture
def text_910b() -> str:
    return (FIXTURES / "npu_smi_info_910b.txt").read_text()


@pytest.fixture
def text_310p() -> str:
    return (FIXTURES / "npu_smi_info_310p.txt").read_text()


def test_parse_910b_devices(text_910b):
    devices = parse_npu_smi_info(text_910b)
    assert len(devices) == 2
    d0, d1 = devices

    assert d0.vendor == "ascend"
    assert d0.index == 0
    assert d0.model == "910B4"
    assert d0.health == 0
    assert d0.power_watts == pytest.approx(88.6)
    assert d0.temp_celsius == pytest.approx(37.0)
    assert d0.util_percent == pytest.approx(0.0)
    assert d0.mem_used_bytes == 3318 * MB
    assert d0.mem_total_bytes == 32768 * MB
    assert d0.ecc_errors_total is None
    assert d0.processes == []

    assert d1.index == 1
    assert d1.health == 1  # Warning
    assert d1.util_percent == pytest.approx(12.0)
    assert d1.mem_used_bytes == 20480 * MB  # irregular "20480/ 32768" spacing
    assert d1.mem_total_bytes == 32768 * MB
    assert d1.processes == [ProcessMem(pid=12345, mem_bytes=18000 * MB)]


def test_parse_310p_falls_back_to_memory_usage(text_310p):
    devices = parse_npu_smi_info(text_310p)
    assert len(devices) == 1
    d = devices[0]
    assert d.model == "310P3"
    assert d.health == 0
    assert d.util_percent == pytest.approx(7.0)
    assert d.mem_used_bytes == 1024 * MB
    assert d.mem_total_bytes == 21527 * MB
    assert d.power_watts == pytest.approx(15.2)
    assert d.temp_celsius == pytest.approx(45.0)
    assert d.processes == []


@pytest.mark.parametrize(
    "health,code",
    [("OK", 0), ("Warning", 1), ("Alarm", 2), ("Critical", 3), ("Unknown", 4), ("--", 4)],
)
def test_health_mapping(text_310p, health, code):
    text = text_310p.replace("| OK            |", f"| {health:<13} |")
    devices = parse_npu_smi_info(text)
    assert devices[0].health == code


def test_parse_empty_output():
    assert parse_npu_smi_info("") == []


def test_collector_available_depends_on_which(monkeypatch):
    monkeypatch.setattr("ai_monitor_agent.collectors.ascend.shutil.which", lambda _: None)
    assert AscendCollector().available() is False
    monkeypatch.setattr(
        "ai_monitor_agent.collectors.ascend.shutil.which", lambda _: "/usr/local/bin/npu-smi"
    )
    assert AscendCollector().available() is True


def test_collector_collect_runs_npu_smi(monkeypatch, text_910b):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=text_910b, stderr="")

    monkeypatch.setattr("ai_monitor_agent.collectors.ascend.subprocess.run", fake_run)
    collector = AscendCollector()
    assert collector.name == "ascend"
    devices = collector.collect()
    assert calls == [["npu-smi", "info"]]
    assert [d.index for d in devices] == [0, 1]


def test_collector_collect_raises_on_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr("ai_monitor_agent.collectors.ascend.subprocess.run", fake_run)
    with pytest.raises(RuntimeError):
        AscendCollector().collect()
