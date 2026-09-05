"""Register files produced by debug tasks."""

from __future__ import annotations

import os
import uuid

from .tasks import Artifact, Task


def register_file(task: Task, path: str, typ: str) -> Artifact:
    art = Artifact(
        id=uuid.uuid4().hex,
        type=typ,
        filename=os.path.basename(path),
        path=os.path.abspath(path),
        size_bytes=os.path.getsize(path) if os.path.isfile(path) else 0,
    )
    task.artifacts.append(art)
    return art


def snapshot_dir(directory: str) -> dict[str, tuple[int, float]]:
    """Map filename -> (size, mtime). Missing dir → empty."""
    if not os.path.isdir(directory):
        return {}
    out: dict[str, tuple[int, float]] = {}
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            st = os.stat(path)
            out[name] = (st.st_size, st.st_mtime)
    return out


def new_or_grown(directory: str, before: dict[str, tuple[int, float]]) -> list[str]:
    after = snapshot_dir(directory)
    found: list[str] = []
    for name, (size, _mtime) in after.items():
        prev = before.get(name)
        if prev is None or size > prev[0]:
            found.append(os.path.join(directory, name))
    return found
