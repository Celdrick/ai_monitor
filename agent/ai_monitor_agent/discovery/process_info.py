"""Read cmdline / cwd / start time / redacted environment for a pid."""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Mapping

import psutil

log = logging.getLogger(__name__)

REDACT_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)", re.I)
REDACTED = "***"


@dataclass
class ProcessInfo:
    cmdline: str | None
    cwd: str | None
    started_at: float | None
    env: dict[str, str] = field(default_factory=dict)


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``env`` with values of sensitive-looking names masked."""
    return {k: (REDACTED if REDACT_RE.search(k) else v) for k, v in env.items()}


def read_process_info(pid: int) -> ProcessInfo:
    """Best-effort process introspection; never raises.

    Each attribute is fetched independently so a permission error on
    ``environ`` (common for root-owned vLLM processes) still leaves cmdline,
    cwd and start time populated.
    """
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return ProcessInfo(cmdline=None, cwd=None, started_at=None, env={})

    cmdline: str | None = None
    cwd: str | None = None
    started_at: float | None = None
    env: dict[str, str] = {}

    try:
        args = proc.cmdline()
        cmdline = shlex.join(args) if args else None
    except psutil.Error as exc:
        log.debug("cmdline(%s) failed: %s", pid, exc)
    try:
        cwd = proc.cwd()
    except psutil.Error as exc:
        log.debug("cwd(%s) failed: %s", pid, exc)
    try:
        started_at = float(proc.create_time())
    except psutil.Error as exc:
        log.debug("create_time(%s) failed: %s", pid, exc)
    try:
        env = redact_env(proc.environ())
    except psutil.Error as exc:
        log.debug("environ(%s) failed: %s", pid, exc)

    return ProcessInfo(cmdline=cmdline, cwd=cwd, started_at=started_at, env=env)
