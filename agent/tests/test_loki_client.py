import asyncio
import json

import httpx
import respx

from ai_monitor_agent.logs.loki_client import LogLine, LokiPusher

LOKI = "http://loki.test:3100"


def _pusher(**kw) -> LokiPusher:
    base = dict(loki_url=LOKI, host="h1", batch_lines=100, batch_interval=0.05, buffer_max_lines=1000)
    base.update(kw)
    return LokiPusher(**base)


def test_build_payload_groups_by_service_source_level_and_keeps_order():
    p = _pusher()
    batch = [
        ("svc", "file", LogLine(ts_ns=100, line="INFO a", level="info")),
        ("svc", "file", LogLine(ts_ns=100, line="ERROR b", level="error")),
        ("svc", "file", LogLine(ts_ns=100, line="Traceback", level="error")),
        ("svc", "file", LogLine(ts_ns=90, line="  File x", level="error")),  # out of order ts
        ("svc", "file", LogLine(ts_ns=200, line="INFO c", level="info")),
        ("other", "docker", LogLine(ts_ns=50, line="INFO d", level="info")),
    ]
    payload = p.build_payload(batch)
    assert set(payload) == {"streams"}
    streams = payload["streams"]
    assert [s["stream"] for s in streams] == [
        {"host": "h1", "service": "svc", "source": "file", "level": "info"},
        {"host": "h1", "service": "svc", "source": "file", "level": "error"},
        {"host": "h1", "service": "other", "source": "docker", "level": "info"},
    ]
    info_vals = streams[0]["values"]
    assert info_vals == [["100", "INFO a"], ["200", "INFO c"]]
    err_vals = streams[1]["values"]
    assert [v[1] for v in err_vals] == ["ERROR b", "Traceback", "  File x"]
    ts = [int(v[0]) for v in err_vals]
    assert ts == [100, 101, 102]  # equal/backwards timestamps bumped by 1ns
    assert all(isinstance(v[0], str) for v in err_vals)


def test_enqueue_drops_oldest_when_full_and_counts():
    p = _pusher(buffer_max_lines=3)
    for i in range(5):
        p.enqueue("svc", "file", LogLine(ts_ns=i, line=f"l{i}", level="info"))
    assert p.dropped == {"svc": 2}
    assert p.pending() == 3
    p.enqueue("other", "file", LogLine(ts_ns=9, line="x", level="info"))
    assert p.dropped == {"svc": 3}


@respx.mock
async def test_flush_posts_json_and_clears_buffer():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(204))
    p = _pusher()
    p.enqueue("svc", "file", LogLine(ts_ns=1, line="INFO a", level="info"))
    p.enqueue("svc", "file", LogLine(ts_ns=2, line="WARNING b", level="warning"))
    async with httpx.AsyncClient() as client:
        assert await p.flush(client) is True
        assert await p.flush(client) is True  # nothing to send is fine
    assert route.call_count == 1
    req = route.calls.last.request
    assert req.headers["content-type"].startswith("application/json")
    body = json.loads(req.content)
    assert len(body["streams"]) == 2
    assert body["streams"][0]["values"] == [["1", "INFO a"]]
    assert p.pending() == 0
    assert p.backoff == 0


@respx.mock
async def test_flush_failure_keeps_data_and_backs_off_then_recovers():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(500, text="nope"))
    p = _pusher()
    p.enqueue("svc", "file", LogLine(ts_ns=1, line="a", level="info"))
    async with httpx.AsyncClient() as client:
        assert await p.flush(client) is False
        assert p.pending() == 1
        assert p.backoff == 1
        assert await p.flush(client) is False
        assert p.backoff == 2
        for _ in range(10):
            await p.flush(client)
        assert p.backoff == 30
        assert p.pending() == 1
        route.mock(side_effect=httpx.ConnectError("refused"))
        assert await p.flush(client) is False
        assert p.pending() == 1

        route.mock(return_value=httpx.Response(204))
        assert await p.flush(client) is True
    assert p.pending() == 0
    assert p.backoff == 0
    body = json.loads(route.calls.last.request.content)
    assert body["streams"][0]["values"] == [["1", "a"]]


@respx.mock
async def test_flush_sends_at_most_batch_lines_per_call():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(204))
    p = _pusher(batch_lines=2)
    for i in range(5):
        p.enqueue("svc", "file", LogLine(ts_ns=i, line=str(i), level="info"))
    async with httpx.AsyncClient() as client:
        assert await p.flush(client) is True
        assert p.pending() == 3
    body = json.loads(route.calls.last.request.content)
    assert body["streams"][0]["values"] == [["0", "0"], ["1", "1"]]


@respx.mock
async def test_failed_batch_is_requeued_in_front_and_trimmed():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(500))
    p = _pusher(batch_lines=10, buffer_max_lines=3)
    for i in range(3):
        p.enqueue("svc", "file", LogLine(ts_ns=i, line=str(i), level="info"))
    async with httpx.AsyncClient() as client:
        await p.flush(client)
        # buffer still 0,1,2; new lines push out the oldest
        p.enqueue("svc", "file", LogLine(ts_ns=3, line="3", level="info"))
        assert p.pending() == 3
        assert p.dropped == {"svc": 1}
        route.mock(return_value=httpx.Response(204))
        await p.flush(client)
    body = json.loads(route.calls.last.request.content)
    assert [v[1] for v in body["streams"][0]["values"]] == ["1", "2", "3"]


@respx.mock
async def test_run_flushes_on_interval_and_on_stop():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(204))
    p = _pusher(batch_interval=0.05)
    stop = asyncio.Event()
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(p.run(stop, client))
        p.enqueue("svc", "file", LogLine(ts_ns=1, line="a", level="info"))
        await asyncio.sleep(0.2)
        assert route.call_count >= 1
        assert p.pending() == 0
        p.enqueue("svc", "file", LogLine(ts_ns=2, line="b", level="info"))
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    assert p.pending() == 0  # final flush on stop
    assert route.call_count >= 2


@respx.mock
async def test_run_flushes_early_when_batch_full():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(204))
    p = _pusher(batch_lines=3, batch_interval=10.0)
    stop = asyncio.Event()
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(p.run(stop, client))
        await asyncio.sleep(0.01)
        for i in range(3):
            p.enqueue("svc", "file", LogLine(ts_ns=i, line=str(i), level="info"))
        await asyncio.sleep(0.2)
        assert route.call_count == 1
        stop.set()
        await asyncio.wait_for(task, timeout=2)


@respx.mock
async def test_run_respects_backoff_between_attempts():
    route = respx.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(500))
    p = _pusher(batch_interval=0.01)
    stop = asyncio.Event()
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(p.run(stop, client))
        p.enqueue("svc", "file", LogLine(ts_ns=1, line="a", level="info"))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    # first attempt + at most the final flush on stop; backoff (>=1s) prevents hammering
    assert route.call_count <= 2
    assert p.pending() == 1
