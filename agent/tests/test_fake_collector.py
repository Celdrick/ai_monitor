import pytest

from ai_monitor_agent.collectors.fake import FakeCollector

GIB = 1024**3


@pytest.mark.parametrize(
    "vendor,count,mem_total",
    [("nvidia", 2, 80 * GIB), ("ascend", 3, 64 * GIB)],
)
def test_fake_collector_samples(vendor, count, mem_total):
    c = FakeCollector(vendor=vendor, count=count, seed=42)
    assert c.name == f"fake-{vendor}"
    assert c.available() is True
    samples = c.collect()
    assert len(samples) == count
    assert [s.index for s in samples] == list(range(count))
    for s in samples:
        assert s.vendor == vendor
        assert s.model
        assert 0 <= s.util_percent <= 100
        assert s.mem_total_bytes == mem_total
        assert 0 <= s.mem_used_bytes <= s.mem_total_bytes
        assert 30 <= s.temp_celsius <= 80
        assert 50 <= s.power_watts <= 400
        assert len(s.processes) == 1
        assert s.processes[0].pid > 0
        assert 0 < s.processes[0].mem_bytes <= s.mem_used_bytes or s.mem_used_bytes == 0
        if vendor == "nvidia":
            assert s.ecc_errors_total is not None and s.ecc_errors_total >= 0
            assert s.health == 0
        else:
            assert s.ecc_errors_total is None
            assert 0 <= s.health <= 4


def test_fake_collector_seed_reproducible():
    a = FakeCollector("nvidia", 2, seed=1).collect()
    b = FakeCollector("nvidia", 2, seed=1).collect()
    assert a == b


def test_fake_collector_rejects_unknown_vendor():
    with pytest.raises(ValueError):
        FakeCollector("amd", 1)
