import asyncio
import re
import socket

import httpx
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from ai_monitor_agent.config import ManualService
from ai_monitor_agent.fake.vllm import FakeVllmServer, fake_vllm_manual_services
from ai_monitor_agent.logs.level_parser import parse_level


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_manual_services_helper(tmp_path):
    services = fake_vllm_manual_services(2, str(tmp_path))
    assert services == [
        ManualService(name="fake-vllm-0", port=18000, log_path=f"{tmp_path}/fake-vllm-0.log"),
        ManualService(name="fake-vllm-1", port=18001, log_path=f"{tmp_path}/fake-vllm-1.log"),
    ]
    assert fake_vllm_manual_services(1, str(tmp_path), base_port=19000)[0].port == 19000
    assert fake_vllm_manual_services(0, str(tmp_path)) == []


def test_fake_app_endpoints(tmp_path):
    server = FakeVllmServer(0, 18000, str(tmp_path / "fake-vllm-0.log"))
    client = TestClient(server.app())
    assert client.get("/v1/models").json() == {"object": "list", "data": [{"id": "fake/Qwen2.5-7B-Instruct", "object": "model"}]}
    assert client.get("/version").json() == {"version": "0.11.0-fake"}

    text = client.get("/metrics").text
    fams = {f.name: f for f in text_string_to_metric_families(text)}
    for name in (
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:kv_cache_usage_perc",
        "vllm:prompt_tokens",
        "vllm:generation_tokens",
        "vllm:request_success",
        "vllm:num_preemptions",
        "vllm:time_to_first_token_seconds",
        "vllm:time_per_output_token_seconds",
        "vllm:e2e_request_latency_seconds",
    ):
        assert name in fams, name
    assert fams["vllm:prompt_tokens"].type == "counter"
    assert fams["vllm:time_to_first_token_seconds"].type == "histogram"
    for fam in fams.values():
        if fam.name.startswith("vllm:"):
            for s in fam.samples:
                assert s.labels.get("model_name") == "fake/Qwen2.5-7B-Instruct", s
    hist = fams["vllm:e2e_request_latency_seconds"].samples
    assert any(s.name.endswith("_bucket") and s.labels["le"] == "+Inf" for s in hist)
    assert any(s.name.endswith("_sum") for s in hist)
    assert any(s.name.endswith("_count") for s in hist)
    assert "vllm:prompt_tokens_total{" in text
    kv = fams["vllm:kv_cache_usage_perc"].samples[0].value
    assert 0.0 <= kv <= 1.0

    # counters are monotonic across scrapes; gauges stay in range
    def counter(t, name):
        return float(re.search(rf"^{re.escape(name)}\{{[^}}]*\}} ([0-9.e+]+)$", t, re.M).group(1))

    t1 = client.get("/metrics").text
    t2 = client.get("/metrics").text
    for name in ("vllm:prompt_tokens_total", "vllm:generation_tokens_total", "vllm:request_success_total"):
        assert counter(t2, name) >= counter(t1, name)
    assert counter(t2, "vllm:num_requests_running") >= 0


async def test_fake_server_run_serves_http_and_writes_logs(tmp_path):
    port = _free_port()
    log_path = tmp_path / "state" / "fake-vllm-0.log"  # parent does not exist yet
    server = FakeVllmServer(0, port, str(log_path))
    server.log_interval = 0.01
    stop = asyncio.Event()
    task = asyncio.create_task(server.run(stop))
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    r = await client.get(f"http://127.0.0.1:{port}/metrics")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.05)
            else:
                raise AssertionError("fake server did not come up")
            assert r.status_code == 200 and "vllm:num_requests_running" in r.text
            r = await client.get(f"http://127.0.0.1:{port}/v1/models")
            assert r.json()["data"][0]["id"] == "fake/Qwen2.5-7B-Instruct"
        for _ in range(200):
            if log_path.exists() and len(log_path.read_text().splitlines()) >= 70:
                break
            await asyncio.sleep(0.02)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    lines = log_path.read_text().splitlines()
    assert len(lines) >= 70
    assert re.match(r"^INFO \d{2}-\d{2} \d{2}:\d{2}:\d{2} \[metrics\.py:100\] Avg prompt throughput: [0-9.]+ tokens/s, ", lines[0])
    warnings = [l for l in lines if l.startswith("WARNING ")]
    assert warnings, "expected a WARNING line every 20 lines"
    err_idx = next(i for i, l in enumerate(lines) if l.startswith("ERROR "))
    assert lines[err_idx + 1] == "Traceback (most recent call last):"
    assert lines[err_idx + 2] == '  File "engine.py", line 1, in step'
    assert lines[err_idx + 3] == '    raise RuntimeError("fake")'
    assert lines[err_idx + 4] == "RuntimeError: fake"
    # the traceback must inherit ERROR through the level parser
    prev = "unknown"
    levels = []
    for l in lines[err_idx : err_idx + 5]:
        prev = parse_level(l, prev)
        levels.append(prev)
    assert levels == ["error"] * 5
