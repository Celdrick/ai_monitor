import pytest

from ai_monitor_server.logs.logql import LEVELS, build_logql, escape_filter, normalize_levels


def test_levels_fixed_set():
    assert LEVELS == ("debug", "info", "warning", "error", "critical", "unknown")


def test_plain_selector():
    assert build_logql("gpu-01", "qwen-72b", [], None) == '{host="gpu-01",service="qwen-72b"}'
    assert build_logql("h", "s", [], "") == '{host="h",service="s"}'


def test_selector_with_levels():
    assert (
        build_logql("h", "s", ["error", "warning"], None)
        == '{host="h",service="s",level=~"error|warning"}'
    )


def test_levels_deduplicated_preserving_order():
    assert build_logql("h", "s", ["error", "error", "info", "error"], None) == (
        '{host="h",service="s",level=~"error|info"}'
    )
    assert normalize_levels(["info", "info"]) == ["info"]


def test_keyword_filter_and_escaping():
    assert build_logql("h", "s", [], "OOM") == '{host="h",service="s"} |= "OOM"'
    assert build_logql("h", "s", ["error"], 'a"b\\c') == (
        '{host="h",service="s",level=~"error"} |= "a\\"b\\\\c"'
    )
    assert escape_filter('a"b\\c') == 'a\\"b\\\\c'
    # backslash escaped first so an injected \" cannot un-escape the quote
    assert escape_filter('\\"') == '\\\\\\"'


@pytest.mark.parametrize("bad", ["", "a b", 'x"}', "a,b", "{", "中文", "a/b"])
def test_illegal_host_or_service(bad):
    with pytest.raises(ValueError):
        build_logql(bad, "s", [], None)
    with pytest.raises(ValueError):
        build_logql("h", bad, [], None)


@pytest.mark.parametrize("bad", ["fatal", "ERROR", "", 'x"}', "warn"])
def test_illegal_level(bad):
    with pytest.raises(ValueError):
        build_logql("h", "s", [bad], None)
    with pytest.raises(ValueError):
        build_logql("h", "s", ["error", bad], None)
