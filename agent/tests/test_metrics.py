import re

import pytest
from prometheus_client import CollectorRegistry

from ai_monitor_agent.collectors.base import HostSample
from ai_monitor_agent.collectors.fake import FakeCollector
from ai_monitor_agent.metrics import AgentMetricsCollector, build_registry, render


class BrokenCollector:
    name = "broken"

    def available(self) -> bool:
        return True

    def collect(self):
        raise RuntimeError("hardware exploded")


class StubHostCollector:
    name = "host"

    def available(self) -> bool:
        return True

    def collect(self) -> HostSample:
        return HostSample(
            cpu_percent=12.5,
            mem_used_bytes=4 * 1024**3,
            mem_total_bytes=16 * 1024**3,
            disks={"/": (100, 1000), "/data": (5, 50)},
            net={"eth0": (111, 222)},
        )


@pytest.fixture
def text() -> str:
    collector = AgentMetricsCollector(
        host="h1",
        device_collectors=[
            FakeCollector("nvidia", 2, seed=1),
            FakeCollector("ascend", 1, seed=2),
            BrokenCollector(),
        ],
        host_collector=StubHostCollector(),
    )
    registry = build_registry(collector)
    assert isinstance(registry, CollectorRegistry)
    return render(registry).decode()


def _lines(text: str, metric: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith(metric + "{") or ln == metric]


def test_device_gauges_have_all_labels(text):
    m = re.search(
        r'^accel_util_percent\{host="h1",index="0",model="[^"]+",vendor="nvidia"\} ([0-9.]+)$',
        text,
        re.M,
    )
    assert m, text
    assert 0 <= float(m.group(1)) <= 100

    for name in (
        "accel_util_percent",
        "accel_mem_used_bytes",
        "accel_mem_total_bytes",
        "accel_temp_celsius",
        "accel_power_watts",
        "accel_health",
    ):
        lines = _lines(text, name)
        assert len(lines) == 3, (name, lines)  # 2 nvidia + 1 ascend
        assert any('vendor="nvidia"' in ln for ln in lines)
        assert any('vendor="ascend"' in ln for ln in lines)
        assert f"# TYPE {name} gauge" in text


def test_ecc_only_for_nvidia(text):
    lines = _lines(text, "accel_ecc_errors_total")
    assert len(lines) == 2
    assert all('vendor="nvidia"' in ln for ln in lines)
    assert "# TYPE accel_ecc_errors_total counter" in text
    assert "accel_ecc_errors_created" not in text


def test_health_zero_for_nvidia(text):
    for ln in _lines(text, "accel_health"):
        if 'vendor="nvidia"' in ln:
            assert ln.endswith(" 0.0")


def test_process_mem_has_pid_label(text):
    lines = _lines(text, "accel_process_mem_bytes")
    assert len(lines) == 3
    assert all(re.search(r'pid="\d+"', ln) for ln in lines)
    assert all('host="h1"' in ln and "index=" in ln and "model=" in ln and "vendor=" in ln for ln in lines)


def test_collector_up_flags(text):
    assert 'agent_collector_up{collector="broken"} 0.0' in text
    assert 'agent_collector_up{collector="fake-nvidia"} 1.0' in text
    assert 'agent_collector_up{collector="fake-ascend"} 1.0' in text
    assert 'agent_collector_up{collector="host"} 1.0' in text


def test_host_metrics(text):
    def value(metric_line_prefix: str) -> float:
        line = next(ln for ln in text.splitlines() if ln.startswith(metric_line_prefix))
        return float(line.rsplit(" ", 1)[1])

    assert value('host_cpu_percent{host="h1"}') == 12.5
    assert value('host_mem_used_bytes{host="h1"}') == 4 * 1024**3
    assert value('host_mem_total_bytes{host="h1"}') == 16 * 1024**3
    assert value('host_disk_used_bytes{host="h1",mount="/"}') == 100
    assert value('host_disk_total_bytes{host="h1",mount="/data"}') == 50
    assert value('host_net_rx_bytes_total{host="h1",iface="eth0"}') == 111
    assert value('host_net_tx_bytes_total{host="h1",iface="eth0"}') == 222
    assert "# TYPE host_net_rx_bytes_total counter" in text
    assert "# TYPE host_net_tx_bytes_total counter" in text
    assert "# TYPE host_disk_used_bytes gauge" in text


def test_no_python_process_metrics(text):
    assert "python_info" not in text
    assert "process_cpu_seconds_total" not in text


def test_broken_host_collector_sets_up_zero():
    class BrokenHost(StubHostCollector):
        def collect(self):
            raise OSError("psutil failed")

    collector = AgentMetricsCollector("h2", [FakeCollector("nvidia", 1)], BrokenHost())
    text = render(build_registry(collector)).decode()
    assert 'agent_collector_up{collector="host"} 0.0' in text
    assert "host_cpu_percent" not in text
    assert 'accel_util_percent{host="h2"' in text


def test_no_host_collector():
    collector = AgentMetricsCollector("h3", [FakeCollector("ascend", 1)], None)
    text = render(build_registry(collector)).decode()
    assert "host_cpu_percent" not in text
    assert 'agent_collector_up{collector="host"}' not in text
    assert 'accel_health{host="h3"' in text
