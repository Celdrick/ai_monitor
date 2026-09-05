"""Command-line entry point: `ai-monitor-agent`."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from dataclasses import dataclass, field

import uvicorn

from . import __version__
from .app import create_app
from .collectors.ascend import AscendCollector
from .collectors.base import DeviceCollector
from .collectors.fake import FakeCollector
from .collectors.host import HostCollector
from .collectors.nvidia import NvidiaCollector
from .config import AgentConfig, load_config
from .discovery.docker import DockerDiscovery
from .discovery.manual import ManualDiscovery
from .discovery.process import ProcessDiscovery
from .discovery.registry import ServiceRegistry
from .fake.vllm import FakeVllmServer, fake_vllm_manual_services

log = logging.getLogger("ai_monitor_agent")

ENV_MAP = {
    "AI_MONITOR_SERVER_URL": "server_url",
    "AI_MONITOR_AGENT_TOKEN": "agent_token",
    "AI_MONITOR_ADVERTISE_ADDRESS": "advertise_address",
    "AI_MONITOR_LISTEN": "listen",
    "AI_MONITOR_LOKI_URL": "loki_url",
    "AI_MONITOR_STATE_DIR": "state_dir",
}


@dataclass
class Runtime:
    config: AgentConfig
    host: str
    device_collectors: list[DeviceCollector]
    host_collector: HostCollector | None
    sources: list = field(default_factory=list)
    fake_vllm: list = field(default_factory=list)
    registry: ServiceRegistry | None = None
    fake_debug: bool = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai-monitor-agent", description="AI Monitor agent")
    parser.add_argument("--config", help="path to agent.yaml")
    parser.add_argument("--fake", type=int, metavar="N", help="serve N synthetic devices instead of real hardware")
    parser.add_argument("--fake-vendor", choices=["nvidia", "ascend"], default="nvidia")
    parser.add_argument("--fake-vllm", type=int, metavar="N", help="start N in-process fake vLLM services")
    parser.add_argument("--fake-debug", action="store_true", help="synthesize py-spy/nsys/msprof artifacts")
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


def _ensure_state_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        log.warning("cannot create state_dir %s: %s", path, exc)


def build_runtime(args: argparse.Namespace) -> Runtime:
    config = _load_settings(args.config)
    host = args.host or socket.gethostname()
    if args.fake is not None:
        device_collectors: list[DeviceCollector] = [FakeCollector(args.fake_vendor, args.fake)]
    else:
        device_collectors = _real_device_collectors()
    if not device_collectors:
        log.warning("no accelerator collectors available; exporting host metrics only")

    fake_mode = args.fake is not None or args.fake_vllm
    fake_servers: list[FakeVllmServer] = []
    manuals = list(config.services)
    if args.fake_vllm:
        _ensure_state_dir(config.state_dir)
        extra = fake_vllm_manual_services(args.fake_vllm, config.state_dir)
        manuals.extend(extra)
        fake_servers = [
            FakeVllmServer(i, extra[i].port, extra[i].log_path or "") for i in range(args.fake_vllm)
        ]

    sources: list = [ManualDiscovery(manuals)]
    if not fake_mode and config.discovery.docker:
        sources.append(DockerDiscovery())
    if not fake_mode and config.discovery.process:
        sources.append(ProcessDiscovery())
    registry = ServiceRegistry(sources)
    return Runtime(
        config=config,
        host=host,
        device_collectors=device_collectors,
        host_collector=HostCollector(),
        sources=sources,
        fake_vllm=fake_servers,
        registry=registry,
        fake_debug=bool(args.fake_debug),
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    rt = build_runtime(args)
    log.info(
        "starting ai-monitor-agent %s host=%s listen=%s collectors=%s fake_vllm=%s",
        __version__,
        rt.host,
        rt.config.listen,
        [c.name for c in rt.device_collectors],
        len(rt.fake_vllm),
    )
    app = create_app(
        rt.config,
        rt.device_collectors,
        rt.host_collector,
        host=rt.host,
        registry=rt.registry,
        fake_vllm=rt.fake_vllm,
        fake_debug=rt.fake_debug,
    )
    uvicorn.run(app, host=rt.config.listen_host, port=rt.config.listen_port, log_level=str(args.log_level).lower())


if __name__ == "__main__":
    main()
