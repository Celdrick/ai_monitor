import pytest

from ai_monitor_agent.logs.level_parser import LEVELS, parse_level


def test_levels_constant():
    assert LEVELS == ("debug", "info", "warning", "error", "critical", "unknown")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("INFO 09-04 16:00:00 [engine.py:123] Started engine", "info"),
        ("WARNING 09-04 16:00:01 [scheduler.py:1] Preempting", "warning"),
        ("ERROR 09-04 16:00:02 [engine.py:9] boom", "error"),
        ("DEBUG 09-04 16:00:02 [x.py:1] dbg", "debug"),
        ("CRITICAL 09-04 16:00:02 [x.py:1] fatal", "critical"),
    ],
)
def test_vllm_format(line, expected):
    assert parse_level(line) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("INFO:     127.0.0.1:1234 - \"GET /metrics HTTP/1.1\" 200 OK", "info"),
        ("WARNING:  uvicorn worker restarting", "warning"),
        ("ERROR:    Exception in ASGI application", "error"),
    ],
)
def test_uvicorn_format(line, expected):
    assert parse_level(line) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("2026-09-04 16:00:00,123 [ERROR] x failed", "error"),
        ("2026-09-04 16:00:00 root WARN something", "warning"),
        ("2026-09-04 16:00:00 root FATAL something", "critical"),
        ("[2026-09-04T16:00:00] INFO my.module: ok", "info"),
        ("2026-09-04 16:00:00 DEBUG dbg", "debug"),
    ],
)
def test_python_logging_format(line, expected):
    assert parse_level(line) == expected


def test_python_logging_keyword_beyond_64_chars_is_not_matched():
    line = "x" * 70 + " ERROR here"
    assert parse_level(line) == "unknown"


@pytest.mark.parametrize(
    "line",
    [
        "Traceback (most recent call last):",
        '  File "engine.py", line 1, in step',
        '    raise RuntimeError("fake")',
        "RuntimeError: fake",
        "ValueError: bad",
        "SomeCustomException: details",
        "\tindented tab line",
    ],
)
def test_continuation_inherits_prev_level(line):
    assert parse_level(line, "error") == "error"
    assert parse_level(line, "warning") == "warning"
    assert parse_level(line, "unknown") == "unknown"


def test_unknown_lines():
    assert parse_level("hello world") == "unknown"
    assert parse_level("hello world", "error") == "unknown"  # not a continuation
    assert parse_level("") == "unknown"
    assert parse_level("Information about stuff") == "unknown"  # INFO must be a whole word


def test_vllm_format_beats_prev_level():
    assert parse_level("INFO 09-04 16:00:00 [x.py:1] back to normal", "error") == "info"
