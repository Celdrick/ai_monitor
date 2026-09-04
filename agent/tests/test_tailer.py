import asyncio
import json
import os
import time
from datetime import datetime, timezone

from ai_monitor_agent.logs.cursors import CursorStore
from ai_monitor_agent.logs.loki_client import LogLine
from ai_monitor_agent.logs.tailer import DockerTailer, FileTailer, LogManager, parse_docker_line
from ai_monitor_agent.services import ServiceInfo


class FakePusher:
    def __init__(self):
        self.lines: list[tuple[str, str, LogLine]] = []
        self.dropped: dict[str, int] = {}

    def enqueue(self, service, source, line):
        self.lines.append((service, source, line))


async def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


# --- CursorStore ------------------------------------------------------------


def test_cursor_store_roundtrip_and_missing_file(tmp_path):
    path = tmp_path / "sub" / "log_cursors.json"
    store = CursorStore(str(path))
    assert store.get("file:/x") is None
    store.set("file:/x", {"inode": 1, "offset": 10})
    store.save()
    assert json.loads(path.read_text()) == {"file:/x": {"inode": 1, "offset": 10}}
    again = CursorStore(str(path))
    assert again.get("file:/x") == {"inode": 1, "offset": 10}


def test_cursor_store_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "log_cursors.json"
    path.write_text("{not json")
    store = CursorStore(str(path))
    assert store.get("k") is None
    store.set("k", {"a": 1})
    store.save()
    assert json.loads(path.read_text()) == {"k": {"a": 1}}


async def test_cursor_store_run_persists_periodically(tmp_path):
    path = tmp_path / "log_cursors.json"
    store = CursorStore(str(path))
    stop = asyncio.Event()
    task = asyncio.create_task(store.run(stop, interval=0.05))
    store.set("k", {"v": 1})
    assert await _wait_for(lambda: path.exists())
    assert json.loads(path.read_text()) == {"k": {"v": 1}}
    store.set("k", {"v": 2})
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert json.loads(path.read_text()) == {"k": {"v": 2}}  # final save on stop


# --- FileTailer -------------------------------------------------------------


async def test_file_tailer_reads_appended_lines_and_tracks_cursor(tmp_path):
    log_path = tmp_path / "svc.log"
    log_path.write_text("INFO 09-04 16:00:00 [a.py:1] one\nINFO 09-04 16:00:01 [a.py:1] two\nERROR 09-04 16:00:02 [a.py:1] three\n")
    pusher = FakePusher()
    cursors = CursorStore(str(tmp_path / "cursors.json"))
    tailer = FileTailer("svc", str(log_path), pusher, cursors)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(stop, poll_interval=0.02))
    assert await _wait_for(lambda: len(pusher.lines) == 3)
    assert [l.line for _, _, l in pusher.lines] == [
        "INFO 09-04 16:00:00 [a.py:1] one",
        "INFO 09-04 16:00:01 [a.py:1] two",
        "ERROR 09-04 16:00:02 [a.py:1] three",
    ]
    assert [l.level for _, _, l in pusher.lines] == ["info", "info", "error"]
    assert all(s == "svc" and src == "file" for s, src, _ in pusher.lines)
    assert pusher.lines[0][2].ts_ns > 1_600_000_000 * 10**9
    cur = cursors.get(f"file:{log_path}")
    assert cur["offset"] == log_path.stat().st_size
    assert cur["inode"] == log_path.stat().st_ino

    # continuation lines inherit the previous level; partial line waits for newline
    with log_path.open("a") as fh:
        fh.write("Traceback (most recent call last):\n  File \"x\"\nRuntimeError: bad\nINFO 09-04 16:00:03 [a.py:1] half")
        fh.flush()
    assert await _wait_for(lambda: len(pusher.lines) == 6)
    await asyncio.sleep(0.1)
    assert len(pusher.lines) == 6
    assert [l.level for _, _, l in pusher.lines[3:]] == ["error", "error", "error"]
    with log_path.open("a") as fh:
        fh.write(" done\n")
    assert await _wait_for(lambda: len(pusher.lines) == 7)
    assert pusher.lines[6][2].line == "INFO 09-04 16:00:03 [a.py:1] half done"
    assert pusher.lines[6][2].level == "info"
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert cursors.get(f"file:{log_path}")["offset"] == log_path.stat().st_size


async def test_file_tailer_resumes_from_cursor_without_duplicates(tmp_path):
    log_path = tmp_path / "svc.log"
    log_path.write_text("a\nb\n")
    cursors_path = tmp_path / "cursors.json"
    cursors = CursorStore(str(cursors_path))
    cursors.set(f"file:{log_path}", {"inode": log_path.stat().st_ino, "offset": 2})
    pusher = FakePusher()
    tailer = FileTailer("svc", str(log_path), pusher, cursors)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(stop, poll_interval=0.02))
    assert await _wait_for(lambda: len(pusher.lines) == 1)
    assert pusher.lines[0][2].line == "b"
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_file_tailer_handles_rotation_and_missing_file(tmp_path):
    log_path = tmp_path / "svc.log"
    pusher = FakePusher()
    cursors = CursorStore(str(tmp_path / "cursors.json"))
    tailer = FileTailer("svc", str(log_path), pusher, cursors)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(stop, poll_interval=0.02))
    await asyncio.sleep(0.1)  # file does not exist yet; tailer must keep waiting
    assert pusher.lines == []
    log_path.write_text("one\ntwo\n")
    assert await _wait_for(lambda: len(pusher.lines) == 2)

    # rotate: move aside and create a new (different inode) file
    os.rename(log_path, tmp_path / "svc.log.1")
    await asyncio.sleep(0.05)
    log_path.write_text("new1\n")
    assert await _wait_for(lambda: len(pusher.lines) == 3)
    assert pusher.lines[2][2].line == "new1"

    # truncate in place (size < offset) → read from 0 again
    log_path.write_text("t\n")
    assert await _wait_for(lambda: len(pusher.lines) == 4)
    assert pusher.lines[3][2].line == "t"
    stop.set()
    await asyncio.wait_for(task, timeout=2)


# --- DockerTailer -----------------------------------------------------------


def test_parse_docker_line():
    base_ns = int(datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc).timestamp()) * 10**9
    ts, msg = parse_docker_line("2026-09-04T08:00:00.123456789Z INFO 09-04 hello")
    assert msg == "INFO 09-04 hello"
    assert ts == base_ns + 123456789
    ts2, msg2 = parse_docker_line("garbage line without timestamp")
    assert msg2 == "garbage line without timestamp"
    assert abs(ts2 - time.time_ns()) < 5 * 10**9
    ts3, msg3 = parse_docker_line("2026-09-04T08:00:00Z bare-seconds")
    assert msg3 == "bare-seconds" and ts3 == base_ns
    ts4, msg4 = parse_docker_line("2026-09-04T10:00:00.5+02:00 offset")
    assert msg4 == "offset" and ts4 == base_ns + 500_000_000


class FakeContainer:
    def __init__(self, chunks, cid="cid123"):
        self.id = cid
        self._chunks = chunks
        self.calls = []

    def logs(self, **kw):
        self.calls.append(kw)
        for chunk in self._chunks:
            yield chunk
        # after the canned chunks the stream "ends" (e.g. container stopped)


async def test_docker_tailer_parses_timestamps_and_updates_cursor(tmp_path):
    chunks = [
        b"2026-09-04T08:00:00.000000001Z INFO 09-04 16:00:00 [a.py:1] one\n",
        b"2026-09-04T08:00:01.000000002Z ERROR 09-04 16:00:01 [a.py:1] two\n2026-09-04T08:00:01.000000003Z Traceback (most",
        b" recent call last):\n",
    ]
    container = FakeContainer(chunks)
    pusher = FakePusher()
    cursors = CursorStore(str(tmp_path / "cursors.json"))
    tailer = DockerTailer("svc", container, pusher, cursors)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(stop, reconnect_delay=0.05))
    assert await _wait_for(lambda: len(pusher.lines) == 3)
    stop.set()
    await asyncio.wait_for(task, timeout=3)
    lines = [l for _, _, l in pusher.lines]
    assert [l.line for l in lines] == [
        "INFO 09-04 16:00:00 [a.py:1] one",
        "ERROR 09-04 16:00:01 [a.py:1] two",
        "Traceback (most recent call last):",
    ]
    assert [l.level for l in lines] == ["info", "error", "error"]
    assert lines[1].ts_ns - lines[0].ts_ns == 10**9 + 1
    assert all(src == "docker" for _, src, _ in pusher.lines)
    first = container.calls[0]
    assert first["stream"] is True and first["follow"] is True and first["timestamps"] is True
    assert isinstance(first["since"], int)
    cur = cursors.get("docker:cid123")
    assert cur["ts_ns"] == lines[2].ts_ns
    assert cur["since"] == lines[2].ts_ns // 10**9


async def test_docker_tailer_resume_skips_already_seen_lines(tmp_path):
    chunks = [
        b"2026-09-04T08:00:00.000000001Z old\n",
        b"2026-09-04T08:00:00.000000005Z new\n",
    ]
    container = FakeContainer(chunks)
    pusher = FakePusher()
    cursors = CursorStore(str(tmp_path / "cursors.json"))
    seen_ns = int(datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc).timestamp()) * 10**9 + 1
    cursors.set("docker:cid123", {"since": seen_ns // 10**9, "ts_ns": seen_ns})
    tailer = DockerTailer("svc", container, pusher, cursors)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(stop, reconnect_delay=0.05))
    assert await _wait_for(lambda: len(pusher.lines) == 1)
    stop.set()
    await asyncio.wait_for(task, timeout=3)
    assert pusher.lines[0][2].line == "new"
    assert container.calls[0]["since"] == seen_ns // 10**9


# --- LogManager -------------------------------------------------------------


class FakeRegistry:
    def __init__(self, services):
        self.services = services

    def snapshot(self):
        return list(self.services)


class FakeDockerClient:
    def __init__(self, containers):
        self._containers = containers
        self.containers = self

    def get(self, cid):
        return self._containers[cid]


async def test_log_manager_starts_and_stops_tailers(tmp_path):
    file_log = tmp_path / "f.log"
    file_log.write_text("INFO 09-04 16:00:00 [a.py:1] file-line\n")
    container = FakeContainer([b"2026-09-04T08:00:00.000000001Z docker-line\n"])
    file_svc = ServiceInfo(name="fsvc", source="manual", port=1, metrics_url="http://x", log_source="file", log_path=str(file_log))
    docker_svc = ServiceInfo(
        name="dsvc", source="docker", port=2, metrics_url="http://x", log_source="docker", container_id="cid123"
    )
    none_svc = ServiceInfo(name="nsvc", source="process", port=3, metrics_url="http://x", log_source="none")
    registry = FakeRegistry([file_svc, docker_svc, none_svc])
    pusher = FakePusher()
    cursors = CursorStore(str(tmp_path / "cursors.json"))
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return FakeDockerClient({"cid123": container})

    manager = LogManager(registry, pusher, cursors, docker_client_factory=factory)
    stop = asyncio.Event()
    task = asyncio.create_task(manager.run(stop, interval=0.05))
    assert await _wait_for(lambda: len(pusher.lines) == 2)
    got = {(s, src, l.line) for s, src, l in pusher.lines}
    assert got == {("fsvc", "file", "INFO 09-04 16:00:00 [a.py:1] file-line"), ("dsvc", "docker", "docker-line")}
    assert set(manager.active()) == {"fsvc", "dsvc"}
    assert len(factory_calls) == 1

    # service disappears → its tailer is cancelled
    registry.services = [docker_svc]
    assert await _wait_for(lambda: set(manager.active()) == {"dsvc"})
    with file_log.open("a") as fh:
        fh.write("INFO 09-04 16:00:01 [a.py:1] should-not-be-read\n")
    await asyncio.sleep(0.15)
    assert len(pusher.lines) == 2

    stop.set()
    await asyncio.wait_for(task, timeout=3)
    assert manager.active() == []


async def test_log_manager_survives_docker_client_failure(tmp_path):
    docker_svc = ServiceInfo(
        name="dsvc", source="docker", port=2, metrics_url="http://x", log_source="docker", container_id="cid123"
    )
    registry = FakeRegistry([docker_svc])
    pusher = FakePusher()
    cursors = CursorStore(str(tmp_path / "cursors.json"))

    def factory():
        raise FileNotFoundError("no socket")

    manager = LogManager(registry, pusher, cursors, docker_client_factory=factory)
    stop = asyncio.Event()
    task = asyncio.create_task(manager.run(stop, interval=0.05))
    await asyncio.sleep(0.15)
    assert manager.active() == []
    stop.set()
    await asyncio.wait_for(task, timeout=2)
