import os

import psutil

from ai_monitor_agent.discovery.process_info import ProcessInfo, read_process_info, redact_env


def test_redact_env_masks_sensitive_names_case_insensitively():
    out = redact_env(
        {
            "HF_TOKEN": "x",
            "PATH": "/bin",
            "api_key": "k",
            "MySecretThing": "s",
            "DB_PASSWORD": "p",
            "passwd": "q",
            "MODEL": "qwen",
        }
    )
    assert out["HF_TOKEN"] == "***"
    assert out["api_key"] == "***"
    assert out["MySecretThing"] == "***"
    assert out["DB_PASSWORD"] == "***"
    assert out["passwd"] == "***"
    assert out["PATH"] == "/bin"
    assert out["MODEL"] == "qwen"


def test_redact_env_returns_new_dict():
    src = {"HF_TOKEN": "x"}
    out = redact_env(src)
    assert out is not src
    assert src["HF_TOKEN"] == "x"


def test_read_process_info_self():
    info = read_process_info(os.getpid())
    assert isinstance(info, ProcessInfo)
    assert info.cmdline
    assert "python" in info.cmdline.lower() or "pytest" in info.cmdline.lower()
    assert info.started_at is not None and info.started_at > 0
    assert info.cwd == os.getcwd()
    assert isinstance(info.env, dict)
    # /proc/<pid>/environ is the *initial* environment; any sensitive-looking
    # names present must already be masked.
    for k, v in info.env.items():
        if any(tok in k.upper() for tok in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")):
            assert v == "***", k


def test_read_process_info_missing_pid():
    # a pid that (almost certainly) doesn't exist
    info = read_process_info(2**22 - 1)
    assert info == ProcessInfo(cmdline=None, cwd=None, started_at=None, env={})


def test_read_process_info_access_denied_env(monkeypatch):
    real = psutil.Process

    class Denied(real):
        def environ(self):
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(psutil, "Process", Denied)
    info = read_process_info(os.getpid())
    assert info.cmdline
    assert info.env == {}
