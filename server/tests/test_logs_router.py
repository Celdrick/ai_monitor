import httpx
import pytest
import respx

from ai_monitor_server.logs.loki_client import LokiClient, LokiError, ns_to_iso, parse_streams

LOKI = "http://loki.test:3100"

NS = 1_757_000_000_000_000_000  # 2026-09-04T...Z-ish base timestamp in ns

LOKI_RESPONSE = {
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {"host": "gpu-01", "service": "qwen-72b", "level": "error"},
                "values": [
                    [str(NS + 2_000_000_000), "ERROR 09-04 boom"],
                    [str(NS + 2_000_000_500), "Traceback (most recent call last):"],
                ],
            },
            {
                "stream": {"host": "gpu-01", "service": "qwen-72b", "level": "info"},
                "values": [
                    [str(NS + 1_000_000_000), "INFO 09-04 first"],
                    [str(NS + 3_000_000_000), "INFO 09-04 last"],
                ],
            },
        ],
    },
}


@pytest.fixture
def loki_mock():
    with respx.mock(base_url=LOKI, assert_all_called=False) as mock:
        yield mock


def test_ns_to_iso():
    assert ns_to_iso(0) == "1970-01-01T00:00:00+00:00"
    assert ns_to_iso(1_500_000_000_123_456_789) == "2017-07-14T02:40:00.123456+00:00"


def test_parse_streams_merges_and_sorts():
    rows = parse_streams(LOKI_RESPONSE["data"]["result"])
    assert [r["line"] for r in rows] == [
        "INFO 09-04 first",
        "ERROR 09-04 boom",
        "Traceback (most recent call last):",
        "INFO 09-04 last",
    ]
    assert [r["level"] for r in rows] == ["info", "error", "error", "info"]
    assert rows == sorted(rows, key=lambda r: r["ts"])
    assert set(rows[0]) == {"ts", "level", "line"}
    # missing level label -> unknown; garbage tolerated
    assert parse_streams([{"stream": {}, "values": [["1", "x"], ["bad", "y"], []]}]) == [
        {"ts": ns_to_iso(1), "level": "unknown", "line": "x"}
    ]
    assert parse_streams(None) == []


async def test_query_logs_merges_streams(client, admin_headers, loki_mock):
    route = loki_mock.get("/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=LOKI_RESPONSE)
    )
    resp = await client.get(
        "/api/logs/query",
        params={
            "host": "gpu-01",
            "service": "qwen-72b",
            "level": ["error", "info"],
            "q": 'a"b',
            "start": 1_757_000_000,
            "end": 1_757_000_100.5,
            "limit": 50,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_more"] is False
    assert [l["level"] for l in body["lines"]] == ["info", "error", "error", "info"]
    assert body["lines"][0]["line"] == "INFO 09-04 first"
    assert body["lines"][0]["ts"] == ns_to_iso(NS + 1_000_000_000)

    sent = route.calls.last.request.url.params
    assert sent["query"] == '{host="gpu-01",service="qwen-72b",level=~"error|info"} |= "a\\"b"'
    assert sent["start"] == str(1_757_000_000 * 10**9)
    assert sent["end"] == str(int(1_757_000_100.5 * 1e9))
    assert sent["limit"] == "50"
    assert sent["direction"] == "backward"


async def test_query_logs_defaults_last_hour_and_has_more(client, admin_headers, loki_mock):
    two_lines = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {"stream": {"level": "info"}, "values": [[str(NS), "a"], [str(NS + 1), "b"]]}
            ],
        },
    }
    route = loki_mock.get("/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=two_lines)
    )
    resp = await client.get(
        "/api/logs/query",
        params={"host": "h", "service": "s", "limit": 2, "direction": "forward"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_more"] is True
    sent = route.calls.last.request.url.params
    start, end = int(sent["start"]), int(sent["end"])
    assert end - start == 3600 * 10**9
    assert sent["direction"] == "forward"
    assert sent["query"] == '{host="h",service="s"}'


async def test_query_logs_limit_capped_at_5000(client, admin_headers, loki_mock):
    route = loki_mock.get("/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"result": []}})
    )
    resp = await client.get(
        "/api/logs/query",
        params={"host": "h", "service": "s", "limit": 999999},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert route.calls.last.request.url.params["limit"] == "5000"
    assert resp.json() == {"lines": [], "has_more": False}


async def test_query_logs_bad_level_400(client, admin_headers, loki_mock):
    route = loki_mock.get("/loki/api/v1/query_range")
    resp = await client.get(
        "/api/logs/query",
        params={"host": "h", "service": "s", "level": "fatal"},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert not route.called


async def test_query_logs_bad_host_400(client, admin_headers, loki_mock):
    resp = await client.get(
        "/api/logs/query",
        params={"host": 'h"}', "service": "s"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


async def test_query_logs_bad_direction_422(client, admin_headers, loki_mock):
    resp = await client.get(
        "/api/logs/query",
        params={"host": "h", "service": "s", "direction": "sideways"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


async def test_query_logs_loki_500_is_502(client, admin_headers, loki_mock):
    loki_mock.get("/loki/api/v1/query_range").mock(return_value=httpx.Response(500, text="boom"))
    resp = await client.get(
        "/api/logs/query", params={"host": "h", "service": "s"}, headers=admin_headers
    )
    assert resp.status_code == 502
    assert "detail" in resp.json()


async def test_query_logs_loki_unreachable_is_502(client, admin_headers, loki_mock):
    loki_mock.get("/loki/api/v1/query_range").mock(side_effect=httpx.ConnectError("refused"))
    resp = await client.get(
        "/api/logs/query", params={"host": "h", "service": "s"}, headers=admin_headers
    )
    assert resp.status_code == 502


async def test_query_logs_requires_login(client, loki_mock):
    resp = await client.get("/api/logs/query", params={"host": "h", "service": "s"})
    assert resp.status_code == 401


async def test_loki_client_direct(loki_mock):
    loki_mock.get("/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=LOKI_RESPONSE)
    )
    c = LokiClient(LOKI)
    try:
        rows = await c.query_range('{host="h"}', 1, 2, 10, "backward")
        assert len(rows) == 4
    finally:
        await c.aclose()


async def test_loki_client_error_status(loki_mock):
    loki_mock.get("/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json={"status": "error", "error": "parse error"})
    )
    c = LokiClient(LOKI)
    try:
        with pytest.raises(LokiError):
            await c.query_range("{}", 1, 2, 10, "backward")
    finally:
        await c.aclose()


def test_tail_url():
    c = LokiClient("http://loki:3100/")
    url = c.tail_url('{host="h",service="s"}', start_ns=123, limit=100)
    assert url.startswith("ws://loki:3100/loki/api/v1/tail?")
    assert "query=%7Bhost%3D%22h%22%2Cservice%3D%22s%22%7D" in url
    assert "delay_for=0" in url
    assert "limit=100" in url
    assert "start=123" in url
    assert LokiClient("https://loki.example:443").tail_url("{}", 1).startswith("wss://loki.example:443/")
