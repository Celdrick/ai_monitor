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

S = '{host="$host",service="$service"}'
SVC = ("host", "service")
SVC_W = ("host", "service", "window")


def _q(bucket: str) -> str:
    return " or ".join(
        f"label_replace(histogram_quantile({phi}, sum by (le) (rate({bucket}[$window])))"
        f', "q", "{label}", "", "")'
        for phi, label in (("0.5", "p50"), ("0.9", "p90"), ("0.99", "p99"))
    )


EXPECTED_VLLM = {
    "vllm_running": (SVC, f"vllm:num_requests_running{S}"),
    "vllm_waiting": (SVC, f"vllm:num_requests_waiting{S}"),
    "vllm_kv_cache_perc": (
        SVC,
        f"(vllm:kv_cache_usage_perc{S} or vllm:gpu_cache_usage_perc{S}) * 100",
    ),
    "vllm_prompt_tokens_rate": (SVC_W, f"sum(rate(vllm:prompt_tokens_total{S}[$window]))"),
    "vllm_generation_tokens_rate": (
        SVC_W,
        f"sum(rate(vllm:generation_tokens_total{S}[$window]))",
    ),
    "vllm_request_rate": (SVC_W, f"sum(rate(vllm:request_success_total{S}[$window]))"),
    "vllm_preemption_rate": (SVC_W, f"sum(rate(vllm:num_preemptions_total{S}[$window]))"),
    "vllm_ttft_quantiles": (SVC_W, _q(f"vllm:time_to_first_token_seconds_bucket{S}")),
    "vllm_tpot_quantiles": (
        SVC_W,
        _q(
            f"(vllm:time_per_output_token_seconds_bucket{S}"
            f" or vllm:inter_token_latency_seconds_bucket{S})"
        ),
    ),
    "vllm_e2e_quantiles": (SVC_W, _q(f"vllm:e2e_request_latency_seconds_bucket{S}")),
    "vllm_scrape_ok": (SVC, f"agent_vllm_scrape_success{S}"),
    "cluster_vllm_running_by_service": ((), "sum by (host, service) (vllm:num_requests_running)"),
    "cluster_vllm_waiting_by_service": ((), "sum by (host, service) (vllm:num_requests_waiting)"),
}


def test_template_catalog_matches_plan():
    assert set(TEMPLATES) == set(EXPECTED) | set(EXPECTED_VLLM)
    assert len(TEMPLATES) == 17 + 13
    for name, (params, expr) in {**EXPECTED, **EXPECTED_VLLM}.items():
        assert isinstance(TEMPLATES[name], Template)
        assert TEMPLATES[name].params == params
        assert TEMPLATES[name].expr == expr
    for name, tpl in TEMPLATES.items():
        if "window" in tpl.params:
            assert tpl.defaults == {"window": "1m"}, name
        else:
            assert dict(tpl.defaults) == {}, name


def test_render_vllm_quantiles_default_window():
    out = render("vllm_ttft_quantiles", {"host": "h", "service": "s"})
    assert "[1m]" in out
    assert out.count("label_replace(") == 3
    assert out.count('{host="h",service="s"}') == 3
    for q in ("p50", "p90", "p99"):
        assert f'"q", "{q}", "", ""' in out
    assert "$" not in out


def test_render_vllm_window_override():
    out = render("vllm_prompt_tokens_rate", {"host": "h", "service": "s", "window": "5m"})
    assert out == 'sum(rate(vllm:prompt_tokens_total{host="h",service="s"}[5m]))'
    out = render("vllm_tpot_quantiles", {"host": "h", "service": "s", "window": "30m"})
    assert out.count("[30m]") == 3
    assert "vllm:time_per_output_token_seconds_bucket" in out
    assert "vllm:inter_token_latency_seconds_bucket" in out


@pytest.mark.parametrize("bad", ["5x", "5", "m", "1d", "5 m", "1m)", "", "-1m"])
def test_render_vllm_window_rejected(bad):
    with pytest.raises(ValueError):
        render("vllm_request_rate", {"host": "h", "service": "s", "window": bad})


def test_render_vllm_requires_service():
    with pytest.raises(ValueError):
        render("vllm_running", {"host": "h"})
    with pytest.raises(ValueError):
        render("vllm_running", {"host": "h", "service": 'a"} or {b="'})


def test_render_vllm_service_selector():
    assert (
        render("vllm_running", {"host": "gpu-01", "service": "qwen-72b"})
        == 'vllm:num_requests_running{host="gpu-01",service="qwen-72b"}'
    )
    assert (
        render("vllm_kv_cache_perc", {"host": "h", "service": "s"})
        == '(vllm:kv_cache_usage_perc{host="h",service="s"}'
        ' or vllm:gpu_cache_usage_perc{host="h",service="s"}) * 100'
    )


def test_render_cluster_vllm_no_params():
    assert (
        render("cluster_vllm_running_by_service", {})
        == "sum by (host, service) (vllm:num_requests_running)"
    )
    assert (
        render("cluster_vllm_waiting_by_service", {"host": "x", "window": "5x"})
        == "sum by (host, service) (vllm:num_requests_waiting)"
    )


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
