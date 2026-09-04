import asyncio

import httpx
import respx
from prometheus_client.parser import text_string_to_metric_families

from ai_monitor_agent.discovery.registry import ServiceRegistry
from ai_monitor_agent.metrics import AgentMetricsCollector, build_registry, render
from ai_monitor_agent.services import ServiceInfo
from ai_monitor_agent.vllm.metrics_proxy import VllmMetricsProxy, relabel_vllm_text

VLLM_TEXT = """# HELP python_gc_objects_collected_total Objects collected during gc
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 1.0
# HELP process_cpu_seconds_total Total user and system CPU time spent in seconds.
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total 12.0
# HELP vllm:num_requests_running Number of requests currently running on GPU.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="m"} 3.0
# HELP vllm:prompt_tokens_total Number of prefill tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total{model_name="m"} 100.0
# HELP vllm:e2e_request_latency_seconds Histogram of e2e request latency in seconds.
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_bucket{le="1.0",model_name="m"} 5.0
vllm:e2e_request_latency_seconds_bucket{le="+Inf",model_name="m"} 7.0
vllm:e2e_request_latency_seconds_count{model_name="m"} 7.0
vllm:e2e_request_latency_seconds_sum{model_name="m"} 3.5
# TYPE vllm:cache_config_info gauge
vllm:cache_config_info{block_size="16",quote="a\\"b"} 1.0
"""


def test_relabel_filters_and_adds_labels():
    out = relabel_vllm_text(VLLM_TEXT, host="h", service="s")
    assert "python_" not in out
    assert "process_cpu" not in out
    assert 'vllm:num_requests_running{host="h",service="s",model_name="m"} 3.0' in out
    assert 'vllm:prompt_tokens_total{host="h",service="s",model_name="m"} 100.0' in out
    assert 'vllm:e2e_request_latency_seconds_bucket{host="h",service="s",le="1.0",model_name="m"} 5.0' in out
    assert 'vllm:e2e_request_latency_seconds_bucket{host="h",service="s",le="+Inf",model_name="m"} 7.0' in out
    assert 'vllm:e2e_request_latency_seconds_count{host="h",service="s",model_name="m"} 7.0' in out
    assert 'vllm:e2e_request_latency_seconds_sum{host="h",service="s",model_name="m"} 3.5' in out
    # HELP/TYPE exactly once each, with full counter name (_total) restored
    assert out.count("# TYPE vllm:prompt_tokens_total counter") == 1
    assert out.count("# HELP vllm:prompt_tokens_total Number of prefill tokens processed.") == 1
    assert out.count("# TYPE vllm:e2e_request_latency_seconds histogram") == 1
    assert out.count("# TYPE vllm:num_requests_running gauge") == 1
    assert "# TYPE vllm:prompt_tokens counter" not in out
    assert "# HELP vllm:cache_config_info" not in out  # no doc → no HELP line
    assert 'quote="a\\"b"' in out
    assert out.endswith("\n")


def test_relabel_output_is_reparseable():
    out = relabel_vllm_text(VLLM_TEXT, host="h", service="s")
    fams = {f.name: f for f in text_string_to_metric_families(out)}
    assert set(fams) == {
        "vllm:num_requests_running",
        "vllm:prompt_tokens",
        "vllm:e2e_request_latency_seconds",
        "vllm:cache_config_info",
    }
    assert fams["vllm:prompt_tokens"].type == "counter"
    assert fams["vllm:e2e_request_latency_seconds"].type == "histogram"
    for fam in fams.values():
        for s in fam.samples:
            assert s.labels["host"] == "h" and s.labels["service"] == "s"
    assert fams["vllm:cache_config_info"].samples[0].labels["quote"] == 'a"b'


def test_relabel_garbage_returns_empty():
    assert relabel_vllm_text("this is not { prometheus", "h", "s") == ""
    assert relabel_vllm_text("", "h", "s") == ""


class StaticSource:
    name = "manual"

    def __init__(self, services):
        self._services = services

    def discover(self):
        return list(self._services)


def _registry(*services: ServiceInfo) -> ServiceRegistry:
    reg = ServiceRegistry([StaticSource(list(services))], probe=lambda url: (None, None), process_info=lambda pid: None)
    reg.refresh()
    return reg


def _svc(name, port):
    return ServiceInfo(name=name, source="manual", port=port, metrics_url=f"http://127.0.0.1:{port}")


@respx.mock
async def test_refresh_once_two_services_one_failing():
    respx.get("http://127.0.0.1:8001/metrics").mock(return_value=httpx.Response(200, text=VLLM_TEXT))
    respx.get("http://127.0.0.1:8002/metrics").mock(return_value=httpx.Response(500, text="boom"))
    reg = _registry(_svc("ok", 8001), _svc("bad", 8002))
    proxy = VllmMetricsProxy(reg, host="h1")
    async with httpx.AsyncClient() as client:
        await proxy.refresh_once(client)
    assert proxy.scrape_status() == {"ok": True, "bad": False}
    text = proxy.render().decode()
    assert 'vllm:num_requests_running{host="h1",service="ok",model_name="m"} 3.0' in text
    assert 'service="bad"' not in text
    assert "python_" not in text
    snap = {s.name: s for s in reg.snapshot()}
    assert snap["ok"].scrape_ok is True and snap["bad"].scrape_ok is False


@respx.mock
async def test_refresh_once_connection_error_and_disappearing_service():
    route = respx.get("http://127.0.0.1:8001/metrics").mock(return_value=httpx.Response(200, text=VLLM_TEXT))
    respx.get("http://127.0.0.1:8002/metrics").mock(side_effect=httpx.ConnectError("refused"))
    src = StaticSource([_svc("a", 8001), _svc("b", 8002)])
    reg = ServiceRegistry([src], probe=lambda url: (None, None), process_info=lambda pid: None)
    reg.refresh()
    proxy = VllmMetricsProxy(reg, host="h1", timeout=1.0)
    async with httpx.AsyncClient() as client:
        await proxy.refresh_once(client)
        assert proxy.scrape_status() == {"a": True, "b": False}
        # service "a" disappears; its cached text must go away too
        src._services = [_svc("b", 8002)]
        reg.refresh()
        await proxy.refresh_once(client)
    assert proxy.scrape_status() == {"b": False}
    assert proxy.render() == b""
    assert route.called


@respx.mock
async def test_run_loops_until_stopped():
    route = respx.get("http://127.0.0.1:8001/metrics").mock(return_value=httpx.Response(200, text=VLLM_TEXT))
    proxy = VllmMetricsProxy(_registry(_svc("a", 8001)), host="h1")
    stop = asyncio.Event()
    task = asyncio.create_task(proxy.run(stop, interval=0.05))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert route.call_count >= 2
    assert 'service="a"' in proxy.render().decode()


@respx.mock
async def test_agent_metrics_include_vllm_status_and_dropped():
    respx.get("http://127.0.0.1:8001/metrics").mock(return_value=httpx.Response(200, text=VLLM_TEXT))
    respx.get("http://127.0.0.1:8002/metrics").mock(return_value=httpx.Response(503))
    reg = _registry(_svc("ok", 8001), _svc("bad", 8002))
    proxy = VllmMetricsProxy(reg, host="h1")
    async with httpx.AsyncClient() as client:
        await proxy.refresh_once(client)

    class FakePusher:
        dropped = {"ok": 7}

    collector = AgentMetricsCollector("h1", [], None, proxy=proxy, pusher=FakePusher())
    text = render(build_registry(collector)).decode()
    assert 'agent_vllm_scrape_success{host="h1",service="ok"} 1.0' in text
    assert 'agent_vllm_scrape_success{host="h1",service="bad"} 0.0' in text
    assert 'agent_vllm_services{host="h1"} 2.0' in text
    assert 'agent_log_lines_dropped_total{host="h1",service="ok"} 7.0' in text
    assert "# TYPE agent_log_lines_dropped_total counter" in text


def test_agent_metrics_without_proxy_emit_zero_services():
    collector = AgentMetricsCollector("h1", [], None)
    text = render(build_registry(collector)).decode()
    assert 'agent_vllm_services{host="h1"} 0.0' in text
    assert "agent_vllm_scrape_success{" not in text
    assert "agent_log_lines_dropped_total{" not in text
