import pytest

from ai_monitor_server.metrics.templates import TEMPLATES, Template, render

EXPECTED = {
    "cluster_device_count": ((), "count(accel_mem_total_bytes)"),
    "cluster_avg_util": ((), "avg(accel_util_percent)"),
    "cluster_mem_used": ((), "sum(accel_mem_used_bytes)"),
    "cluster_mem_total": ((), "sum(accel_mem_total_bytes)"),
    "cluster_util_by_host": ((), "avg by (host) (accel_util_percent)"),
    "host_devices": (("host",), 'accel_mem_total_bytes{host="$host"}'),
    "host_device_util": (("host",), 'accel_util_percent{host="$host"}'),
    "host_device_mem_used": (("host",), 'accel_mem_used_bytes{host="$host"}'),
    "host_device_mem_total": (("host",), 'accel_mem_total_bytes{host="$host"}'),
    "host_device_temp": (("host",), 'accel_temp_celsius{host="$host"}'),
    "host_device_power": (("host",), 'accel_power_watts{host="$host"}'),
    "host_device_process_mem": (("host",), 'accel_process_mem_bytes{host="$host"}'),
    "host_cpu": (("host",), 'host_cpu_percent{host="$host"}'),
    "host_mem_used": (("host",), 'host_mem_used_bytes{host="$host"}'),
    "host_mem_total": (("host",), 'host_mem_total_bytes{host="$host"}'),
    "host_disk_used": (("host",), 'host_disk_used_bytes{host="$host"}'),
    "host_disk_total": (("host",), 'host_disk_total_bytes{host="$host"}'),
}


def test_template_catalog_matches_plan():
    assert set(TEMPLATES) == set(EXPECTED)
    assert len(TEMPLATES) == 17
    for name, (params, expr) in EXPECTED.items():
        assert isinstance(TEMPLATES[name], Template)
        assert TEMPLATES[name].params == params
        assert TEMPLATES[name].expr == expr


def test_render_no_params():
    assert render("cluster_avg_util", {}) == "avg(accel_util_percent)"


def test_render_with_host():
    assert render("host_cpu", {"host": "gpu-01"}) == 'host_cpu_percent{host="gpu-01"}'
    assert (
        render("host_device_util", {"host": "node.a_b-1"})
        == 'accel_util_percent{host="node.a_b-1"}'
    )


def test_render_ignores_extra_params():
    assert render("cluster_avg_util", {"host": "x", "index": "0"}) == "avg(accel_util_percent)"


def test_render_unknown_template():
    with pytest.raises(KeyError):
        render("nope", {})


def test_render_missing_param():
    with pytest.raises(ValueError):
        render("host_cpu", {})


@pytest.mark.parametrize(
    "bad",
    ['gpu"} or vector(1) or {x="', "a b", "", "host{", "x}", "a=b", "a,b", "a/b", "中文"],
)
def test_render_rejects_illegal_chars(bad):
    with pytest.raises(ValueError):
        render("host_cpu", {"host": bad})
