"""Classify a log line into one of six fixed levels (spec 4.5)."""

from __future__ import annotations

import re

LEVELS = ("debug", "info", "warning", "error", "critical", "unknown")

_NORMALIZE = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "ERROR": "error",
    "CRITICAL": "critical",
    "FATAL": "critical",
}

# 1. vLLM:    "INFO 09-04 16:00:00 [engine.py:123] ..."
_VLLM_RE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\d{2}-\d{2}")
# 2. uvicorn: "INFO:     127.0.0.1 ..."
_UVICORN_RE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):")
# 3. python logging: level keyword somewhere in the first 64 chars
_PY_RE = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b")
_PY_WINDOW = 64
# 4. continuation lines inherit the previous level
_CONT_RE = re.compile(r'^(\s|Traceback|File "|\w+(Error|Exception):)')


def parse_level(line: str, prev_level: str = "unknown") -> str:
    m = _VLLM_RE.match(line)
    if m:
        return _NORMALIZE[m.group(1)]
    m = _UVICORN_RE.match(line)
    if m:
        return _NORMALIZE[m.group(1)]
    m = _PY_RE.search(line[:_PY_WINDOW])
    if m:
        return _NORMALIZE[m.group(1)]
    if line and _CONT_RE.match(line):
        return prev_level if prev_level in LEVELS else "unknown"
    return "unknown"
