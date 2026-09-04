"""WebSocket tail proxy tests.

A fake Loki WebSocket server (``websockets.serve``) runs in its own thread with
its own event loop, because ``starlette.testclient.TestClient`` drives the ASGI
app synchronously from the test thread.
"""

import asyncio
import json
import threading
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from ai_monitor_server.auth.security import create_refresh_token
from ai_monitor_server.logs.loki_client import LokiClient, ns_to_iso

NS = 1_757_000_000_000_000_000

FRAMES = [
    {
        "streams": [
            {
                "stream": {"host": "h", "service": "s", "level": "warning"},
                "values": [[str(NS + 2_000_000_000), "WARNING 09-04 slow"]],
            },
            {
                "stream": {"host": "h", "service": "s", "level": "info"},
                "values": [[str(NS + 1_000_000_000), "INFO 09-04 hello"]],
            },
        ]
    },
    {
        "streams": [
            {
                "stream": {"host": "h", "service": "s", "level": "error"},
                "values": [[str(NS + 3_000_000_000), "ERROR 09-04 boom"]],
            }
        ],
        "dropped_entries": None,
    },
]


class FakeLoki:
    """Minimal Loki ``/loki/api/v1/tail`` stand-in: send frames, then close."""

    def __init__(self, frames):
        self.frames = frames
        self.requests: list[str] = []
        self.port: int | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Future | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def _handler(self, ws):
        self.requests.append(ws.request.path)
        for frame in self.frames:
            await ws.send(json.dumps(frame))
        await ws.close()

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = self._loop.create_future()
        async with serve(self._handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop

    def _run(self):
        asyncio.run(self._main())

    def __enter__(self):
        self._thread.start()
        assert self._ready.wait(5), "fake loki did not start"
        return self

    def __exit__(self, *exc):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def fake_loki():
    with FakeLoki(FRAMES) as server:
        yield server


@pytest.fixture
def tc(app, fake_loki):
    app.state.loki = LokiClient(fake_loki.base_url)
    with TestClient(app) as tc:
        yield tc


def _login(tc: TestClient, username="admin", password="admin123") -> str:
    resp = tc.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_tail_forwards_frames_in_order(tc, fake_loki):
    token = _login(tc)
    received = []
    with tc.websocket_connect(
        f"/api/logs/tail?host=h&service=s&level=info&level=warning&level=error&q=x&token={token}"
    ) as ws:
        for _ in range(3):
            received.append(ws.receive_json())
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1000

    assert [r["level"] for r in received] == ["info", "warning", "error"]
    assert [r["line"] for r in received] == [
        "INFO 09-04 hello",
        "WARNING 09-04 slow",
        "ERROR 09-04 boom",
    ]
    assert received[0]["ts"] == ns_to_iso(NS + 1_000_000_000)
    assert all(set(r) == {"ts", "level", "line"} for r in received)

    # upstream Loki got the server-built LogQL plus tail parameters
    assert len(fake_loki.requests) == 1
    parsed = urlparse(fake_loki.requests[0])
    assert parsed.path == "/loki/api/v1/tail"
    qs = parse_qs(parsed.query)
    assert qs["query"] == ['{host="h",service="s",level=~"info|warning|error"} |= "x"']
    assert qs["delay_for"] == ["0"]
    assert qs["limit"] == ["100"]
    assert int(qs["start"][0]) > 0


def test_tail_two_frames_two_lines_each_counted(tc):
    """The spec scenario: two frames → every line delivered (here 3 total)."""
    token = _login(tc)
    lines = []
    with tc.websocket_connect(f"/api/logs/tail?host=h&service=s&token={token}") as ws:
        try:
            while True:
                lines.append(ws.receive_json())
        except WebSocketDisconnect:
            pass
    assert len(lines) == sum(len(s["values"]) for f in FRAMES for s in f["streams"])


def test_tail_without_token_closes_4401(tc, fake_loki):
    with pytest.raises(WebSocketDisconnect) as closed:
        with tc.websocket_connect("/api/logs/tail?host=h&service=s") as ws:
            ws.receive_json()
    assert closed.value.code == 4401
    assert fake_loki.requests == []


def test_tail_with_garbage_token_closes_4401(tc):
    with pytest.raises(WebSocketDisconnect) as closed:
        with tc.websocket_connect("/api/logs/tail?host=h&service=s&token=nope") as ws:
            ws.receive_json()
    assert closed.value.code == 4401


def test_tail_with_refresh_token_closes_4401(tc, settings):
    refresh = create_refresh_token(1, settings.jwt_secret, 1)
    with pytest.raises(WebSocketDisconnect) as closed:
        with tc.websocket_connect(f"/api/logs/tail?host=h&service=s&token={refresh}") as ws:
            ws.receive_json()
    assert closed.value.code == 4401


def test_tail_bad_level_closes_4400(tc, fake_loki):
    token = _login(tc)
    with pytest.raises(WebSocketDisconnect) as closed:
        with tc.websocket_connect(
            f"/api/logs/tail?host=h&service=s&level=fatal&token={token}"
        ) as ws:
            ws.receive_json()
    assert closed.value.code == 4400
    assert fake_loki.requests == []


def test_tail_loki_unreachable_closes_4502(app):
    app.state.loki = LokiClient("http://127.0.0.1:9")  # discard port: nothing listens
    with TestClient(app) as tc:
        token = _login(tc)
        with pytest.raises(WebSocketDisconnect) as closed:
            with tc.websocket_connect(f"/api/logs/tail?host=h&service=s&token={token}") as ws:
                ws.receive_json()
    assert closed.value.code == 4502
