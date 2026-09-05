"""In-process fake vLLM HTTP server for development and end-to-end tests."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from ..config import ManualService

log = logging.getLogger(__name__)

DEFAULT_MODEL = "fake/Qwen2.5-7B-Instruct"
HIST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class FakeVllmServer:
    """Serves vLLM-shaped ``/metrics``, ``/v1/models``, ``/version`` and writes a log file."""

    def __init__(
        self,
        index: int,
        port: int,
        log_path: str,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.index = index
        self.port = port
        self.log_path = log_path
        self.model = model
        self.profiler_dir = os.path.join(os.path.dirname(log_path) or ".", f"fake-vllm-{index}-profile")
        self.log_interval = 1.0
        self._profiling = False
        self._rng = random.Random(index + 1)
        self._running = 2.0
        self._waiting = 0.0
        self._kv = 0.4
        self._prom = CollectorRegistry()
        labels = {"model_name": model}
        self._g_running = Gauge(
            "vllm:num_requests_running", "Number of requests currently running", ["model_name"], registry=self._prom
        )
        self._g_waiting = Gauge(
            "vllm:num_requests_waiting", "Number of requests currently waiting", ["model_name"], registry=self._prom
        )
        self._g_kv = Gauge(
            "vllm:kv_cache_usage_perc", "KV cache usage fraction (0-1)", ["model_name"], registry=self._prom
        )
        self._c_prompt = Counter(
            "vllm:prompt_tokens", "Prompt tokens processed", ["model_name"], registry=self._prom
        )
        self._c_gen = Counter(
            "vllm:generation_tokens", "Generation tokens produced", ["model_name"], registry=self._prom
        )
        self._c_ok = Counter(
            "vllm:request_success", "Successfully completed requests", ["model_name"], registry=self._prom
        )
        self._c_preempt = Counter(
            "vllm:num_preemptions", "Number of preemptions", ["model_name"], registry=self._prom
        )
        self._h_ttft = Histogram(
            "vllm:time_to_first_token_seconds",
            "Time to first token",
            ["model_name"],
            buckets=HIST_BUCKETS,
            registry=self._prom,
        )
        self._h_tpot = Histogram(
            "vllm:time_per_output_token_seconds",
            "Time per output token",
            ["model_name"],
            buckets=HIST_BUCKETS,
            registry=self._prom,
        )
        self._h_e2e = Histogram(
            "vllm:e2e_request_latency_seconds",
            "End-to-end request latency",
            ["model_name"],
            buckets=HIST_BUCKETS,
            registry=self._prom,
        )
        self._g_running.labels(**labels).set(self._running)
        self._g_waiting.labels(**labels).set(self._waiting)
        self._g_kv.labels(**labels).set(self._kv)

    def _nudge(self) -> None:
        labels = {"model_name": self.model}
        self._running = max(0.0, min(32.0, self._running + self._rng.uniform(-1.5, 1.5)))
        self._waiting = max(0.0, min(64.0, self._waiting + self._rng.uniform(-2.0, 2.5)))
        self._kv = max(0.05, min(0.95, self._kv + self._rng.uniform(-0.04, 0.04)))
        self._g_running.labels(**labels).set(self._running)
        self._g_waiting.labels(**labels).set(self._waiting)
        self._g_kv.labels(**labels).set(self._kv)
        self._c_prompt.labels(**labels).inc(self._rng.randint(8, 64))
        self._c_gen.labels(**labels).inc(self._rng.randint(16, 128))
        self._c_ok.labels(**labels).inc(self._rng.randint(0, 3))
        if self._rng.random() < 0.15:
            self._c_preempt.labels(**labels).inc(1)
        for _ in range(self._rng.randint(1, 4)):
            self._h_ttft.labels(**labels).observe(max(0.001, self._rng.gauss(0.08, 0.04)))
            self._h_tpot.labels(**labels).observe(max(0.001, self._rng.gauss(0.03, 0.01)))
            self._h_e2e.labels(**labels).observe(max(0.01, self._rng.gauss(0.6, 0.25)))

    def app(self) -> FastAPI:
        app = FastAPI(title=f"fake-vllm-{self.index}")

        @app.get("/metrics")
        def metrics() -> Response:
            self._nudge()
            return Response(content=generate_latest(self._prom), media_type=CONTENT_TYPE_LATEST)

        @app.get("/v1/models")
        def models() -> dict[str, Any]:
            return {"object": "list", "data": [{"id": self.model, "object": "model"}]}

        @app.get("/version")
        def version() -> dict[str, str]:
            return {"version": "0.11.0-fake"}

        @app.post("/start_profile")
        def start_profile() -> dict[str, str]:
            self._profiling = True
            return {"status": "ok"}

        @app.post("/stop_profile")
        def stop_profile() -> dict[str, str]:
            if not getattr(self, "_profiling", False):
                return {"status": "ok"}
            self._profiling = False
            directory = self.profiler_dir
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f"fake-trace-{self.index}.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    '{"traceEvents":[{"ph":"X","name":"fake_forward","ts":0,"dur":1000,'
                    f'"pid":{self.index + 1},"tid":1}}],"displayTimeUnit":"ms"}}\n'
                )
            return {"status": "ok", "path": path}

        return app

    def _format_stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%m-%d %H:%M:%S")

    def _next_lines(self, seq: int) -> list[str]:
        stamp = self._format_stamp()
        tps = 8.0 + self._rng.random() * 40.0
        if seq > 0 and seq % 60 == 0:
            return [
                f"ERROR {stamp} [engine.py:1] Engine step failed",
                "Traceback (most recent call last):",
                '  File "engine.py", line 1, in step',
                '    raise RuntimeError("fake")',
                "RuntimeError: fake",
            ]
        if seq > 0 and seq % 20 == 0:
            return [f"WARNING {stamp} [scheduler.py:42] KV cache usage high: {self._kv:.2f}"]
        return [
            f"INFO {stamp} [metrics.py:100] Avg prompt throughput: {tps:.1f} tokens/s, "
            f"generation throughput: {tps * 1.4:.1f} tokens/s"
        ]

    async def _write_logs(self, stop: asyncio.Event) -> None:
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        seq = 0
        while not stop.is_set():
            try:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    for line in self._next_lines(seq):
                        fh.write(line + "\n")
            except OSError as exc:
                log.warning("fake vllm %s cannot write log %s: %s", self.index, self.log_path, exc)
            seq += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.log_interval)
            except asyncio.TimeoutError:
                continue

    async def run(self, stop: asyncio.Event) -> None:
        config = uvicorn.Config(
            self.app(),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        serve_task = asyncio.create_task(self._serve(server), name=f"fake-vllm-{self.index}")
        log_task = asyncio.create_task(self._write_logs(stop), name=f"fake-vllm-log-{self.index}")
        stop_task = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.set()
            server.should_exit = True
            for task in (serve_task, log_task, stop_task):
                if not task.done():
                    task.cancel()
            for task in (serve_task, log_task, stop_task):
                try:
                    await task
                except (asyncio.CancelledError, SystemExit, Exception):
                    pass

    async def _serve(self, server: uvicorn.Server) -> None:
        try:
            await server.serve()
        except SystemExit:
            log.warning("fake vllm %s exited (port %s bind failed?)", self.index, self.port)


def fake_vllm_manual_services(n: int, state_dir: str, base_port: int = 18000) -> list[ManualService]:
    return [
        ManualService(
            name=f"fake-vllm-{i}",
            port=base_port + i,
            log_path=os.path.join(state_dir, f"fake-vllm-{i}.log"),
            profiler_dir=os.path.join(state_dir, f"fake-vllm-{i}-profile"),
        )
        for i in range(n)
    ]
