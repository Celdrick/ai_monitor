import re
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template as _StrTemplate

PARAM_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Template:
    expr: str
    params: tuple[str, ...] = ()


def _host(expr: str) -> Template:
    return Template(expr=expr, params=("host",))


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
}


def render(name: str, params: Mapping[str, str]) -> str:
    """Render a template into PromQL.

    Raises KeyError for an unknown template, ValueError for missing or
    illegal parameter values. Extra parameters are ignored.
    """
    template = TEMPLATES[name]  # KeyError propagates
    values: dict[str, str] = {}
    for param in template.params:
        value = params.get(param)
        if value is None:
            raise ValueError(f"missing parameter {param!r} for template {name!r}")
        if not PARAM_VALUE_RE.match(str(value)):
            raise ValueError(f"illegal value for parameter {param!r}")
        values[param] = str(value)
    return _StrTemplate(template.expr).substitute(values)
