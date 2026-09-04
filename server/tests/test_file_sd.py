import json
import os
from datetime import datetime, timezone

from ai_monitor_server.agents.file_sd import write_file_sd
from ai_monitor_server.models import Agent


def _agent(host, addr, seen=True):
    return Agent(
        host=host,
        advertise_address=addr,
        token_hash=host,
        last_seen_at=datetime.now(timezone.utc) if seen else None,
    )


def test_write_file_sd_creates_dir_and_content(tmp_path):
    path = tmp_path / "nested" / "dir" / "agents.json"
    agents = [_agent("gpu-01", "10.0.0.11:9400"), _agent("npu-01", "10.0.0.12:9400")]
    write_file_sd(agents, str(path))
    assert path.exists()
    data = json.loads(path.read_text())
    assert data == [
        {"targets": ["10.0.0.11:9400"], "labels": {"host": "gpu-01"}},
        {"targets": ["10.0.0.12:9400"], "labels": {"host": "npu-01"}},
    ]
    # no leftover temp files
    assert os.listdir(path.parent) == ["agents.json"]


def test_write_file_sd_empty(tmp_path):
    path = tmp_path / "agents.json"
    write_file_sd([], str(path))
    assert json.loads(path.read_text()) == []


def test_write_file_sd_skips_agents_without_address(tmp_path):
    path = tmp_path / "agents.json"
    write_file_sd([_agent("gpu-01", None)], str(path))
    assert json.loads(path.read_text()) == []


def test_write_file_sd_overwrites(tmp_path):
    path = tmp_path / "agents.json"
    write_file_sd([_agent("gpu-01", "a:1")], str(path))
    write_file_sd([_agent("gpu-02", "b:2")], str(path))
    data = json.loads(path.read_text())
    assert data == [{"targets": ["b:2"], "labels": {"host": "gpu-02"}}]
