import httpx
import respx

from ai_monitor_agent.config import ManualService
from ai_monitor_agent.discovery.manual import ManualDiscovery
from ai_monitor_agent.discovery.process_info import ProcessInfo
from ai_monitor_agent.discovery.registry import ServiceRegistry
from ai_monitor_agent.services import ServiceInfo
from ai_monitor_agent.vllm.probe import fetch_model_and_version


class StaticSource:
    def __init__(self, name, services):
        self.name = name
        self._services = services

    def discover(self):
        return list(self._services)


class BoomSource:
    name = "boom"

    def discover(self):
        raise RuntimeError("discover failed")


def _no_probe(url):
    return (None, None)


def _no_pinfo(pid):
    return ProcessInfo(cmdline=None, cwd=None, started_at=None, env={})


def test_manual_discovery():
    disc = ManualDiscovery([ManualService(name="q", port=8001, log_path="/var/log/q.log", profiler_dir="/p"), ManualService(name="b", port=8002)])
    assert disc.name == "manual"
    a, b = disc.discover()
    assert a == ServiceInfo(
        name="q",
        source="manual",
        port=8001,
        metrics_url="http://127.0.0.1:8001",
        log_source="file",
        log_path="/var/log/q.log",
        profiler_dir="/p",
    )
    assert b.log_source == "none" and b.log_path is None and b.metrics_url == "http://127.0.0.1:8002"


def test_manual_overrides_docker_same_port_and_fills_from_it():
    manual = ManualDiscovery([ManualService(name="qwen-72b", port=8001, log_path="/var/log/q.log", profiler_dir="/p")])
    docker = StaticSource(
        "docker",
        [
            ServiceInfo(
                name="container-x",
                source="docker",
                port=8001,
                metrics_url="http://127.0.0.1:8001",
                pid=77,
                container_id="cid",
                container_name="container-x",
                log_source="docker",
                cmdline="vllm serve m",
                env={"A": "1"},
            )
        ],
    )
    reg = ServiceRegistry([manual, docker], probe=_no_probe, process_info=_no_pinfo)
    services = reg.refresh()
    assert len(services) == 1
    s = services[0]
    assert s.name == "qwen-72b"
    assert s.source == "manual"
    assert s.log_source == "file" and s.log_path == "/var/log/q.log" and s.profiler_dir == "/p"
    assert s.pid == 77 and s.container_id == "cid" and s.container_name == "container-x"
    assert s.cmdline == "vllm serve m" and s.env == {"A": "1"}


def test_manual_without_log_path_inherits_docker_log_source():
    manual = ManualDiscovery([ManualService(name="q", port=8001)])
    docker = StaticSource(
        "docker",
        [ServiceInfo(name="c", source="docker", port=8001, metrics_url="http://172.17.0.5:8000", container_id="cid", log_source="docker")],
    )
    reg = ServiceRegistry([manual, docker], probe=_no_probe, process_info=_no_pinfo)
    (s,) = reg.refresh()
    assert s.name == "q" and s.log_source == "docker"
    # manual metrics_url is a default guess; docker knows the real address
    assert s.metrics_url == "http://172.17.0.5:8000"


def test_docker_beats_process_and_same_pid_process_is_dropped():
    docker = StaticSource(
        "docker",
        [ServiceInfo(name="c", source="docker", port=18000, metrics_url="http://127.0.0.1:18000", pid=100, container_id="cid")],
    )
    process = StaticSource(
        "process",
        [
            ServiceInfo(name="vllm-8000", source="process", port=8000, metrics_url="http://127.0.0.1:8000", pid=100),
            ServiceInfo(name="vllm-8005", source="process", port=8005, metrics_url="http://127.0.0.1:8005", pid=200),
            ServiceInfo(name="vllm-18000", source="process", port=18000, metrics_url="http://127.0.0.1:18000", pid=300),
        ],
    )
    reg = ServiceRegistry([docker, process], probe=_no_probe, process_info=_no_pinfo)
    services = reg.refresh()
    names = sorted(s.name for s in services)
    assert names == ["c", "vllm-8005"]
    c = next(s for s in services if s.name == "c")
    assert c.source == "docker" and c.pid == 100


def test_process_info_fills_missing_fields_and_is_cached_per_pid():
    calls = []

    def pinfo(pid):
        calls.append(pid)
        return ProcessInfo(cmdline="vllm serve m", cwd="/w", started_at=123.0, env={"HF_TOKEN": "***"})

    src = StaticSource("process", [ServiceInfo(name="vllm-8000", source="process", port=8000, metrics_url="http://127.0.0.1:8000", pid=5)])
    reg = ServiceRegistry([src], probe=_no_probe, process_info=pinfo)
    (s,) = reg.refresh()
    assert s.cmdline == "vllm serve m" and s.cwd == "/w" and s.started_at == 123.0 and s.env == {"HF_TOKEN": "***"}
    (s2,) = reg.refresh()
    assert s2.cmdline == "vllm serve m"
    assert calls == [5]


def test_probe_called_until_filled_and_state_preserved():
    answers = iter([(None, None), ("fake/Qwen", "0.11.0"), ("SHOULD-NOT", "CALL")])
    probed = []

    def probe(url):
        probed.append(url)
        return next(answers)

    src = StaticSource("manual", [ServiceInfo(name="q", source="manual", port=8001, metrics_url="http://127.0.0.1:8001")])
    reg = ServiceRegistry([src], probe=probe, process_info=_no_pinfo)
    (s,) = reg.refresh()
    assert s.model is None and s.vllm_version is None
    (s,) = reg.refresh()
    assert s.model == "fake/Qwen" and s.vllm_version == "0.11.0"
    reg.set_scrape_ok("q", True)
    assert reg.snapshot()[0].scrape_ok is True
    (s,) = reg.refresh()
    assert s.model == "fake/Qwen" and s.scrape_ok is True
    assert probed == ["http://127.0.0.1:8001", "http://127.0.0.1:8001"]
    # snapshot returns copies
    snap = reg.snapshot()
    snap[0].name = "mutated"
    assert reg.snapshot()[0].name == "q"


def test_disappeared_service_removed_and_source_errors_ignored():
    holder = StaticSource("manual", [ServiceInfo(name="q", source="manual", port=8001, metrics_url="http://127.0.0.1:8001")])
    reg = ServiceRegistry([holder, BoomSource()], probe=_no_probe, process_info=_no_pinfo)
    assert [s.name for s in reg.refresh()] == ["q"]
    holder._services = []
    assert reg.refresh() == []
    assert reg.snapshot() == []
    reg.set_scrape_ok("nope", True)  # unknown name is a no-op


@respx.mock
def test_fetch_model_and_version():
    respx.get("http://127.0.0.1:8001/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "fake/Qwen2.5-7B-Instruct"}]})
    )
    respx.get("http://127.0.0.1:8001/version").mock(return_value=httpx.Response(200, json={"version": "0.11.0"}))
    assert fetch_model_and_version("http://127.0.0.1:8001") == ("fake/Qwen2.5-7B-Instruct", "0.11.0")


@respx.mock
def test_fetch_model_and_version_partial_failure():
    respx.get("http://127.0.0.1:8001/v1/models").mock(return_value=httpx.Response(500))
    respx.get("http://127.0.0.1:8001/version").mock(return_value=httpx.Response(200, json={"version": "0.11.0"}))
    assert fetch_model_and_version("http://127.0.0.1:8001/") == (None, "0.11.0")


@respx.mock
def test_fetch_model_and_version_connection_error():
    respx.get("http://127.0.0.1:8001/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("http://127.0.0.1:8001/version").mock(side_effect=httpx.ConnectError("refused"))
    assert fetch_model_and_version("http://127.0.0.1:8001") == (None, None)
