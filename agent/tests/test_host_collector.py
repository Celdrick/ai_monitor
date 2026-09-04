from ai_monitor_agent.collectors.base import HostSample
from ai_monitor_agent.collectors.host import HostCollector


def test_host_collector_collects_real_host():
    c = HostCollector()
    assert c.name == "host"
    assert c.available() is True
    sample = c.collect()
    assert isinstance(sample, HostSample)
    assert 0 <= sample.cpu_percent <= 100
    assert sample.mem_total_bytes > 0
    assert 0 <= sample.mem_used_bytes <= sample.mem_total_bytes
    for mount, (used, total) in sample.disks.items():
        assert mount.startswith("/")
        assert 0 <= used <= total
    assert "lo" not in sample.net
    for iface, (rx, tx) in sample.net.items():
        assert iface
        assert rx >= 0 and tx >= 0


def test_host_collector_skips_unreadable_partitions(monkeypatch):
    import psutil

    class Part:
        def __init__(self, mountpoint):
            self.mountpoint = mountpoint

    def fake_partitions(all=False):
        assert all is False
        return [Part("/"), Part("/broken")]

    real_usage = psutil.disk_usage

    def fake_usage(path):
        if path == "/broken":
            raise PermissionError("nope")
        return real_usage("/")

    monkeypatch.setattr(psutil, "disk_partitions", fake_partitions)
    monkeypatch.setattr(psutil, "disk_usage", fake_usage)
    sample = HostCollector().collect()
    assert "/" in sample.disks
    assert "/broken" not in sample.disks
