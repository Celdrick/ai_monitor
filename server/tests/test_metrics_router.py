import httpx
import pytest
import respx

from ai_monitor_server.metrics.vm_client import VMClient, VMError

VM = "http://vm.test:8428"


@pytest.fixture
def vm_mock():
    with respx.mock(base_url=VM, assert_all_called=False) as mock:
        yield mock


async def test_query_passthrough(client, admin_headers, vm_mock):
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {"host": "gpu-01"}, "value": [1700000000, "42"]}],
        },
    }
    route = vm_mock.get("/api/v1/query").mock(return_value=httpx.Response(200, json=payload))
    resp = await client.get(
        "/api/metrics/query",
        params={"template": "host_cpu", "host": "gpu-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == payload["data"]
    assert route.called
    sent = route.calls.last.request
    assert sent.url.params["query"] == 'host_cpu_percent{host="gpu-01"}'


async def test_query_with_time(client, admin_headers, vm_mock):
    route = vm_mock.get("/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
    )
    resp = await client.get(
        "/api/metrics/query",
        params={"template": "cluster_avg_util", "time": "1700000000"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert route.calls.last.request.url.params["time"] == "1700000000"


async def test_query_range_passthrough(client, admin_headers, vm_mock):
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"index": "0"}, "values": [[1700000000, "1"], [1700000015, "2"]]}
            ],
        },
    }
    route = vm_mock.get("/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=payload)
    )
    resp = await client.get(
        "/api/metrics/query_range",
        params={
            "template": "host_device_util",
            "host": "gpu-01",
            "start": 1700000000,
            "end": 1700003600,
            "step": "15s",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == payload["data"]
    sent = route.calls.last.request.url.params
    assert sent["query"] == 'accel_util_percent{host="gpu-01"}'
    assert sent["start"] == "1700000000"
    assert sent["end"] == "1700003600"
    assert sent["step"] == "15s"


async def test_query_requires_login(client, vm_mock):
    resp = await client.get("/api/metrics/query", params={"template": "cluster_avg_util"})
    assert resp.status_code == 401


async def test_query_unknown_template_400(client, admin_headers, vm_mock):
    route = vm_mock.get("/api/v1/query")
    resp = await client.get(
        "/api/metrics/query", params={"template": "nope"}, headers=admin_headers
    )
    assert resp.status_code == 400
    assert not route.called


async def test_query_missing_param_400(client, admin_headers, vm_mock):
    resp = await client.get(
        "/api/metrics/query", params={"template": "host_cpu"}, headers=admin_headers
    )
    assert resp.status_code == 400


async def test_query_illegal_param_400(client, admin_headers, vm_mock):
    resp = await client.get(
        "/api/metrics/query",
        params={"template": "host_cpu", "host": 'a"} or {b="'},
        headers=admin_headers,
    )
    assert resp.status_code == 400


async def test_query_range_missing_range_params_422(client, admin_headers, vm_mock):
    resp = await client.get(
        "/api/metrics/query_range",
        params={"template": "host_cpu", "host": "gpu-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


async def test_vm_500_returns_502(client, admin_headers, vm_mock):
    vm_mock.get("/api/v1/query").mock(return_value=httpx.Response(500, text="boom"))
    resp = await client.get(
        "/api/metrics/query", params={"template": "cluster_avg_util"}, headers=admin_headers
    )
    assert resp.status_code == 502
    assert "detail" in resp.json()


async def test_vm_error_status_returns_502(client, admin_headers, vm_mock):
    vm_mock.get("/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "errorType": "bad_data", "error": "parse error"}
        )
    )
    resp = await client.get(
        "/api/metrics/query", params={"template": "cluster_avg_util"}, headers=admin_headers
    )
    assert resp.status_code == 502
    assert "parse error" in resp.json()["detail"]


async def test_vm_unreachable_returns_502(client, admin_headers, vm_mock):
    vm_mock.get("/api/v1/query").mock(side_effect=httpx.ConnectError("refused"))
    resp = await client.get(
        "/api/metrics/query", params={"template": "cluster_avg_util"}, headers=admin_headers
    )
    assert resp.status_code == 502


async def test_vm_client_direct(vm_mock):
    vm_mock.get("/api/v1/query").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"x": 1}})
    )
    vm_mock.get("/api/v1/query_range").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"y": 2}})
    )
    c = VMClient(VM)
    try:
        assert await c.query("up") == {"x": 1}
        assert await c.query_range("up", 1, 2, "15s") == {"y": 2}
    finally:
        await c.aclose()


async def test_vm_client_raises_on_non_json(vm_mock):
    vm_mock.get("/api/v1/query").mock(return_value=httpx.Response(200, text="not json"))
    c = VMClient(VM)
    try:
        with pytest.raises(VMError):
            await c.query("up")
    finally:
        await c.aclose()
