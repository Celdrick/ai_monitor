"""Prometheus exporter that turns collector samples into metric families."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from .collectors.base import DeviceCollector, DeviceSample, HostSample
from .collectors.host import HostCollector

if TYPE_CHECKING:  # avoid import cycles at runtime; duck-typed below
    from .logs.loki_client import LokiPusher
    from .vllm.metrics_proxy import VllmMetricsProxy

log = logging.getLogger(__name__)

DEVICE_LABELS = ["host", "index", "model", "vendor"]
PROCESS_LABELS = DEVICE_LABELS + ["pid"]


class AgentMetricsCollector:
    """A prometheus_client custom collector; each scrape runs every collector."""

    def __init__(
        self,
        host: str,
        device_collectors: list[DeviceCollector],
        host_collector: HostCollector | None,
        proxy: "VllmMetricsProxy | None" = None,
        pusher: "LokiPusher | None" = None,
    ) -> None:
        self.host = host
        self.device_collectors = list(device_collectors)
        self.host_collector = host_collector
        self.proxy = proxy
        self.pusher = pusher

    # prometheus_client calls describe() at registration; returning [] means
    # "no static description", which avoids running collectors on register.
    def describe(self) -> list[Metric]:
        return []

    def collect(self) -> Iterable[Metric]:
        up = GaugeMetricFamily(
            "agent_collector_up", "1 if the collector succeeded on this scrape", labels=["collector"]
        )

        device_samples: list[DeviceSample] = []
        for collector in self.device_collectors:
            try:
                device_samples.extend(collector.collect())
                up.add_metric([collector.name], 1)
            except Exception as exc:
                log.warning("collector %s failed: %s", collector.name, exc)
                up.add_metric([collector.name], 0)

        host_sample: HostSample | None = None
        if self.host_collector is not None:
            try:
                host_sample = self.host_collector.collect()
                up.add_metric([self.host_collector.name], 1)
            except Exception as exc:
                log.warning("collector %s failed: %s", self.host_collector.name, exc)
                up.add_metric([self.host_collector.name], 0)

        yield from self._device_families(device_samples)
        if host_sample is not None:
            yield from self._host_families(host_sample)
        yield up
        yield from self._service_families()

    def _service_families(self) -> Iterable[Metric]:
        services = GaugeMetricFamily("agent_vllm_services", "Number of vLLM services known to this agent", labels=["host"])
        scrape = GaugeMetricFamily(
            "agent_vllm_scrape_success", "1 if the last vLLM /metrics scrape succeeded", labels=["host", "service"]
        )
        dropped = CounterMetricFamily(
            "agent_log_lines_dropped", "Log lines dropped because the Loki buffer was full", labels=["host", "service"]
        )
        count = 0
        if self.proxy is not None:
            try:
                count = len(self.proxy.registry.snapshot())
                for name, ok in sorted(self.proxy.scrape_status().items()):
                    scrape.add_metric([self.host, name], 1 if ok else 0)
            except Exception as exc:
                log.warning("vllm proxy status failed: %s", exc)
        services.add_metric([self.host], count)
        if self.pusher is not None:
            for name, n in sorted(self.pusher.dropped.items()):
                dropped.add_metric([self.host, name], n)
        yield services
        yield scrape
        if self.pusher is not None:
            yield dropped

    def _device_families(self, samples: list[DeviceSample]) -> Iterable[Metric]:
        util = GaugeMetricFamily("accel_util_percent", "Accelerator utilization percent", labels=DEVICE_LABELS)
        mem_used = GaugeMetricFamily("accel_mem_used_bytes", "Accelerator memory used in bytes", labels=DEVICE_LABELS)
        mem_total = GaugeMetricFamily("accel_mem_total_bytes", "Accelerator memory total in bytes", labels=DEVICE_LABELS)
        temp = GaugeMetricFamily("accel_temp_celsius", "Accelerator temperature in Celsius", labels=DEVICE_LABELS)
        power = GaugeMetricFamily("accel_power_watts", "Accelerator power draw in watts", labels=DEVICE_LABELS)
        health = GaugeMetricFamily(
            "accel_health", "Accelerator health code (Ascend: 0=OK,1=Warning,2=Alarm,3=Critical,4=Unknown; NVIDIA: 0)",
            labels=DEVICE_LABELS,
        )
        ecc = CounterMetricFamily("accel_ecc_errors", "Volatile uncorrected ECC errors (NVIDIA only)", labels=DEVICE_LABELS)
        proc_mem = GaugeMetricFamily("accel_process_mem_bytes", "Accelerator memory used per process", labels=PROCESS_LABELS)

        has_ecc = False
        for s in samples:
            labels = [self.host, str(s.index), s.model, s.vendor]
            util.add_metric(labels, s.util_percent)
            mem_used.add_metric(labels, s.mem_used_bytes)
            mem_total.add_metric(labels, s.mem_total_bytes)
            temp.add_metric(labels, s.temp_celsius)
            power.add_metric(labels, s.power_watts)
            health.add_metric(labels, s.health)
            if s.vendor == "nvidia" and s.ecc_errors_total is not None:
                ecc.add_metric(labels, s.ecc_errors_total)
                has_ecc = True
            for p in s.processes:
                proc_mem.add_metric(labels + [str(p.pid)], p.mem_bytes)

        yield util
        yield mem_used
        yield mem_total
        yield temp
        yield power
        yield health
        if has_ecc:
            yield ecc
        yield proc_mem

    def _host_families(self, h: HostSample) -> Iterable[Metric]:
        host_labels = ["host"]
        cpu = GaugeMetricFamily("host_cpu_percent", "Host CPU utilization percent", labels=host_labels)
        cpu.add_metric([self.host], h.cpu_percent)
        mem_used = GaugeMetricFamily("host_mem_used_bytes", "Host memory used in bytes", labels=host_labels)
        mem_used.add_metric([self.host], h.mem_used_bytes)
        mem_total = GaugeMetricFamily("host_mem_total_bytes", "Host memory total in bytes", labels=host_labels)
        mem_total.add_metric([self.host], h.mem_total_bytes)

        disk_used = GaugeMetricFamily("host_disk_used_bytes", "Disk used in bytes", labels=["host", "mount"])
        disk_total = GaugeMetricFamily("host_disk_total_bytes", "Disk total in bytes", labels=["host", "mount"])
        for mount, (used, total) in sorted(h.disks.items()):
            disk_used.add_metric([self.host, mount], used)
            disk_total.add_metric([self.host, mount], total)

        net_rx = CounterMetricFamily("host_net_rx_bytes", "Network bytes received", labels=["host", "iface"])
        net_tx = CounterMetricFamily("host_net_tx_bytes", "Network bytes transmitted", labels=["host", "iface"])
        for iface, (rx, tx) in sorted(h.net.items()):
            net_rx.add_metric([self.host, iface], rx)
            net_tx.add_metric([self.host, iface], tx)

        yield cpu
        yield mem_used
        yield mem_total
        yield disk_used
        yield disk_total
        yield net_rx
        yield net_tx


def build_registry(collector: AgentMetricsCollector) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(collector)
    return registry


def render(registry: CollectorRegistry) -> bytes:
    return generate_latest(registry)
