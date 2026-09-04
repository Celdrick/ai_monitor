import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from string import Template as _StrTemplate

PARAM_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
WINDOW_RE = re.compile(r"^[0-9]+[smh]$")


@dataclass(frozen=True)
class Template:
    expr: str
    params: tuple[str, ...] = ()
    defaults: Mapping[str, str] = field(default_factory=dict)


def _host(expr: str) -> Template:
    return Template(expr=expr, params=("host",))


# {host="$host",service="$service"} — the per-service selector used by every vLLM template.
_SVC = '{host="$host",service="$service"}'


def _svc(expr: str) -> Template:
    return Template(expr=expr, params=("host", "service"))


def _svc_window(expr: str) -> Template:
    return Template(expr=expr, params=("host", "service", "window"), defaults={"window": "1m"})


def _quantiles(bucket_expr: str) -> Template:
    """p50/p90/p99 of a histogram, each tagged with a ``q`` label and joined by ``or``."""
    parts = [
        f"label_replace(histogram_quantile({phi}, sum by (le) (rate({bucket_expr}[$window])))"
        f', "q", "{label}", "", "")'
        for phi, label in (("0.5", "p50"), ("0.9", "p90"), ("0.99", "p99"))
    ]
    return _svc_window(" or ".join(parts))


TEMPLATES: dict[str, Template] = {
    "cluster_device_count": Template("count(accel_mem_total_bytes)"),
    "cluster_avg_util": Template("avg(accel_util_percent)"),
    "cluster_mem_used": Template("sum(accel_mem_used_bytes)"),
    "cluster_mem_total": Template("sum(accel_mem_total_bytes)"),
    "cluster_util_by_host": Template("avg by (host) (accel_util_percent)"),
    "host_devices": _host('accel_mem_total_bytes{host="$host"}'),
    "host_device_util": _host('accel_util_percent{host="$host"}'),
    "host_device_mem_used": _host('accel_mem_used_bytes{host="$host"}'),
    "host_device_mem_total": _host('accel_mem_total_bytes{host="$host"}'),
    "host_device_temp": _host('accel_temp_celsius{host="$host"}'),
    "host_device_power": _host('accel_power_watts{host="$host"}'),
    "host_device_process_mem": _host('accel_process_mem_bytes{host="$host"}'),
    "host_cpu": _host('host_cpu_percent{host="$host"}'),
    "host_mem_used": _host('host_mem_used_bytes{host="$host"}'),
    "host_mem_total": _host('host_mem_total_bytes{host="$host"}'),
    "host_disk_used": _host('host_disk_used_bytes{host="$host"}'),
    "host_disk_total": _host('host_disk_total_bytes{host="$host"}'),
    # --- vLLM (phase 2) ---
    "vllm_running": _svc(f"vllm:num_requests_running{_SVC}"),
    "vllm_waiting": _svc(f"vllm:num_requests_waiting{_SVC}"),
    "vllm_kv_cache_perc": _svc(
        f"(vllm:kv_cache_usage_perc{_SVC} or vllm:gpu_cache_usage_perc{_SVC}) * 100"
    ),
    "vllm_prompt_tokens_rate": _svc_window(f"sum(rate(vllm:prompt_tokens_total{_SVC}[$window]))"),
    "vllm_generation_tokens_rate": _svc_window(
        f"sum(rate(vllm:generation_tokens_total{_SVC}[$window]))"
    ),
    "vllm_request_rate": _svc_window(f"sum(rate(vllm:request_success_total{_SVC}[$window]))"),
    "vllm_preemption_rate": _svc_window(
        f"sum(rate(vllm:num_preemptions_total{_SVC}[$window]))"
    ),
    "vllm_ttft_quantiles": _quantiles(f"vllm:time_to_first_token_seconds_bucket{_SVC}"),
    "vllm_tpot_quantiles": _quantiles(
        f"(vllm:time_per_output_token_seconds_bucket{_SVC}"
        f" or vllm:inter_token_latency_seconds_bucket{_SVC})"
    ),
    "vllm_e2e_quantiles": _quantiles(f"vllm:e2e_request_latency_seconds_bucket{_SVC}"),
    "vllm_scrape_ok": _svc(f"agent_vllm_scrape_success{_SVC}"),
    "cluster_vllm_running_by_service": Template(
        "sum by (host, service) (vllm:num_requests_running)"
    ),
    "cluster_vllm_waiting_by_service": Template(
        "sum by (host, service) (vllm:num_requests_waiting)"
    ),
}


def render(name: str, params: Mapping[str, str]) -> str:
    """Render a template into PromQL.

    Raises KeyError for an unknown template, ValueError for missing or
    illegal parameter values. Extra parameters are ignored. Parameters with a
    default (e.g. ``window``) may be omitted.
    """
    template = TEMPLATES[name]  # KeyError propagates
    values: dict[str, str] = {}
    for param in template.params:
        value = params.get(param)
        if value is None:
            value = template.defaults.get(param)
        if value is None:
            raise ValueError(f"missing parameter {param!r} for template {name!r}")
        value = str(value)
        pattern = WINDOW_RE if param == "window" else PARAM_VALUE_RE
        if not pattern.match(value):
            raise ValueError(f"illegal value for parameter {param!r}")
        values[param] = value
    return _StrTemplate(template.expr).substitute(values)
