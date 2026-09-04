from types import SimpleNamespace

import psutil

from ai_monitor_agent.discovery.process import ProcessDiscovery, is_vllm_cmdline, port_from_cmdline


def test_is_vllm_cmdline():
    assert is_vllm_cmdline(["vllm", "serve", "Qwen/Qwen2.5-7B"])
    assert is_vllm_cmdline(["python", "-m", "vllm.entrypoints.openai.api_server", "--model", "m"])
    assert is_vllm_cmdline(["/opt/venv/bin/python3", "/opt/venv/bin/vllm", "serve", "m"])
    assert not is_vllm_cmdline(["python", "-c", "import vllm"])  # vllm but no serve/api_server
    assert not is_vllm_cmdline(["nginx", "-g", "daemon off;"])
    assert not is_vllm_cmdline([])


def test_port_from_cmdline():
    assert port_from_cmdline(["vllm", "serve", "m", "--port", "8001"]) == 8001
    assert port_from_cmdline(["vllm", "serve", "m", "--port=8002"]) == 8002
    assert port_from_cmdline(["vllm", "serve", "m"]) is None
    assert port_from_cmdline(["vllm", "serve", "m", "--port"]) is None
    assert port_from_cmdline(["vllm", "serve", "m", "--port", "abc"]) is None


class FakeProc:
    def __init__(self, pid, ppid, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "ppid": ppid, "cmdline": cmdline}


def _conn(pid, port, status="LISTEN"):
    return SimpleNamespace(pid=pid, status=status, laddr=SimpleNamespace(ip="0.0.0.0", port=port), type=1)


def _procs():
    return [
        FakeProc(1, 0, ["/sbin/init"]),
        FakeProc(100, 1, ["bash"]),
        FakeProc(200, 100, ["python", "-m", "vllm.entrypoints.openai.api_server", "--model", "m", "--port", "8001"]),
        FakeProc(201, 200, ["python", "-m", "vllm.entrypoints.openai.api_server", "--model", "m", "--port", "8001"]),  # worker
        FakeProc(202, 201, ["python", "-c", "from multiprocessing.spawn import spawn_main; vllm serve api_server"]),
        FakeProc(300, 1, ["vllm", "serve", "Qwen/Qwen2.5-7B"]),  # port from LISTEN
        FakeProc(400, 1, ["vllm", "serve", "other"]),  # no port info → 8000
        FakeProc(500, 1, ["nginx"]),
    ]


def test_process_discovery_returns_top_level_matches_only():
    conns = [_conn(300, 8100), _conn(300, 8050), _conn(300, 9999, status="ESTABLISHED"), _conn(500, 80)]
    disc = ProcessDiscovery(proc_iter=lambda attrs=None: _procs(), net_connections=lambda kind="inet": conns)
    assert disc.name == "process"
    services = sorted(disc.discover(), key=lambda s: s.pid or 0)
    assert [s.pid for s in services] == [200, 300, 400]
    by_pid = {s.pid: s for s in services}
    assert by_pid[200].port == 8001
    assert by_pid[200].name == "vllm-8001"
    assert by_pid[200].metrics_url == "http://127.0.0.1:8001"
    assert by_pid[200].source == "process"
    assert by_pid[200].log_source == "none"
    assert by_pid[300].port == 8050  # smallest LISTEN port
    assert by_pid[400].port == 8000


def test_process_discovery_excludes_pids_and_their_descendants():
    # pid 200 belongs to a docker container; its worker 201 must not surface either
    disc = ProcessDiscovery(
        proc_iter=lambda attrs=None: _procs(),
        net_connections=lambda kind="inet": [],
        exclude_pids=lambda: {200},
    )
    pids = sorted(s.pid for s in disc.discover())
    assert pids == [300, 400]


def test_process_discovery_survives_errors():
    class Boom:
        pid = 9

        @property
        def info(self):
            raise psutil.NoSuchProcess(pid=self.pid)

    def procs(attrs=None):
        return [Boom(), FakeProc(300, 1, ["vllm", "serve", "m", "--port", "8003"])]

    def conns(kind="inet"):
        raise psutil.AccessDenied()

    disc = ProcessDiscovery(proc_iter=procs, net_connections=conns)
    services = disc.discover()
    assert [s.port for s in services] == [8003]
