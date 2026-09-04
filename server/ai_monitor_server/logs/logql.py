"""LogQL construction. The server is the only place LogQL is assembled; users
never pass raw LogQL."""

import re

LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical", "unknown")
LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _check_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not LABEL_VALUE_RE.match(value):
        raise ValueError(f"illegal value for {name!r}")
    return value


def escape_filter(q: str) -> str:
    """Escape a keyword for use inside a double-quoted LogQL line filter."""
    return q.replace("\\", "\\\\").replace('"', '\\"')


def normalize_levels(levels: list[str]) -> list[str]:
    """Deduplicate (preserving order) and validate against the fixed level set."""
    out: list[str] = []
    for level in levels:
        if level not in LEVELS:
            raise ValueError(f"illegal level {level!r}")
        if level not in out:
            out.append(level)
    return out


def build_logql(host: str, service: str, levels: list[str], q: str | None) -> str:
    """``{host="h",service="s"}`` plus an optional ``level=~`` matcher and ``|=`` filter."""
    matchers = [f'host="{_check_label("host", host)}"', f'service="{_check_label("service", service)}"']
    normalized = normalize_levels(levels)
    if normalized:
        matchers.append(f'level=~"{"|".join(normalized)}"')
    expr = "{" + ",".join(matchers) + "}"
    if q:
        expr += f' |= "{escape_filter(q)}"'
    return expr
