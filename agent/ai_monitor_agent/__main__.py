"""Command-line entry point: `ai-monitor-agent`."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from dataclasses import dataclass

import uvicorn

from . import __version__
from .app import create_app
from .collectors.ascend import AscendCollector
from .collectors.base import DeviceCollector
from .collectors.fake import FakeCollector
from .collectors.host import HostCollector
from .collectors.nvidia import NvidiaCollector
from .config import AgentConfig, load_config

log = logging.getLogger("ai_monitor_agent")

ENV_MAP = {
    "AI_MONITOR_SERVER_URL": "server_url",
    "AI_MONITOR_AGENT_TOKEN": "agent_token",
    "AI_MONITOR_ADVERTISE_ADDRESS": "advertise_address",
    "AI_MONITOR_LISTEN": "listen",
}


@dataclass
class Runtime:
    config: AgentConfig
    host: str
    device_collectors: list[DeviceCollector]
    host_collector: HostCollector | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai-monitor-agent", description="AI Monitor agent")
    parser.add_argument("--config", help="path to agent.yaml")
    parser.add_argument("--fake", type=int, metavar="N", help="serve N synthetic devices instead of real hardware")
    parser.add_argument("--fake-vendor", choices=["nvidia", "ascend"], default="nvidia")
    parser.add_argument("--host", help="host name reported in metrics/heartbeat (default: hostname)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _env_overrides() -> dict[str, str]:
    return {field: os.environ[var] for var, field in ENV_MAP.items() if os.environ.get(var)}


def _load_settings(config_path: str | None) -> AgentConfig:
    overrides = _env_overrides()
    if config_path:
        base = load_config(config_path)
        return base.model_copy(update=overrides) if overrides else base
    missing = [var for var, field in ENV_MAP.items() if field in ("server_url", "agent_token") and field not in overrides]
    if missing:
        sys.exit(
            "error: --config not given and required environment variables are missing: "
            + ", ".join(missing)
        )
    return AgentConfig.model_validate(overrides)


def _real_device_collectors() -> list[DeviceCollector]:
    collectors: list[DeviceCollector] = []
    for candidate in (NvidiaCollector(), AscendCollector()):
        try:
            if candidate.available():
                collectors.append(candidate)
        except Exception as exc:  # defensive: availability probes must not kill startup
            log.warning("collector %s availability check failed: %s", candidate.name, exc)
    return collectors


def build_runtime(args: argparse.Namespace) -> Runtime:
    config = _load_settings(args.config)
    host = args.host or socket.gethostname()
    if args.fake is not None:
        device_collectors: list[DeviceCollector] = [FakeCollector(args.fake_vendor, args.fake)]
    else:
        device_collectors = _real_device_collectors()
    if not device_collectors:
        log.warning("no accelerator collectors available; exporting host metrics only")
    return Runtime(config=config, host=host, device_collectors=device_collectors, host_collector=HostCollector())


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    rt = build_runtime(args)
    log.info(
        "starting ai-monitor-agent %s host=%s listen=%s collectors=%s",
        __version__,
        rt.host,
        rt.config.listen,
        [c.name for c in rt.device_collectors],
    )
    app = create_app(rt.config, rt.device_collectors, rt.host_collector, host=rt.host)
    uvicorn.run(app, host=rt.config.listen_host, port=rt.config.listen_port, log_level=str(args.log_level).lower())


if __name__ == "__main__":
    main()
