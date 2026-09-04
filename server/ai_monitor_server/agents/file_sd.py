import json
import os
import tempfile
from collections.abc import Iterable

from ..models import Agent


def build_file_sd(agents: Iterable[Agent]) -> list[dict]:
    entries = []
    for agent in agents:
        if not agent.advertise_address:
            continue
        entries.append(
            {"targets": [agent.advertise_address], "labels": {"host": agent.host}}
        )
    return entries


def write_file_sd(agents: Iterable[Agent], path: str) -> None:
    """Atomically write a Prometheus file_sd JSON document for the given agents."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps(build_file_sd(agents), indent=2)
    fd, tmp_path = tempfile.mkstemp(prefix=".agents-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
