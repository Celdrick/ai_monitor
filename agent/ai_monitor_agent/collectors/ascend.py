"""Ascend NPU collector backed by the `npu-smi info` command-line tool."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

from .base import DeviceSample, ProcessMem

log = logging.getLogger(__name__)

MB = 1024 * 1024

HEALTH_CODES = {"OK": 0, "Warning": 1, "Alarm": 2, "Critical": 3}
HEALTH_UNKNOWN = 4

_USAGE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# First line of a device block: "| <npu> <Name> | <Health> | <Power> <Temp> <Hugepages used / total> |"
_DEV_LINE1_RE = re.compile(
    r"^\|\s*(?P<npu>\d+)\s+(?P<name>\S+)\s*\|\s*(?P<health>[^|]+?)\s*\|\s*(?P<rest>[^|]*)\|"
)
# Second line: "| <chip> | <Bus-Id> | <AICore%> <Mem used / total> <HBM used / total> |"
_DEV_LINE2_RE = re.compile(
    r"^\|\s*(?P<chip>\d+)\s*\|\s*(?P<bus>[0-9A-Fa-f:.]+)\s*\|\s*(?P<rest>[^|]*)\|"
)
# Process row: "| <npu> <chip> | <pid> | <name> | <mem MB> |"
_PROC_RE = re.compile(
    r"^\|\s*(?P<npu>\d+)\s+(?P<chip>\d+)\s*\|\s*(?P<pid>\d+)\s*\|\s*(?P<name>[^|]*?)\s*\|\s*(?P<mem>\d+)\s*\|"
)


def _parse_float(token: str) -> float:
    m = _NUMBER_RE.search(token)
    return float(m.group(0)) if m else 0.0


def parse_npu_smi_info(text: str) -> list[DeviceSample]:
    """Parse the text output of `npu-smi info` into device samples."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    devices: dict[int, DeviceSample] = {}
    in_process_table = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if "Process id" in line and "Process name" in line:
            in_process_table = True
            i += 1
            continue

        if in_process_table:
            m = _PROC_RE.match(line)
            if m:
                npu = int(m.group("npu"))
                if npu in devices:
                    devices[npu].processes.append(
                        ProcessMem(pid=int(m.group("pid")), mem_bytes=int(m.group("mem")) * MB)
                    )
            i += 1
            continue

        m1 = _DEV_LINE1_RE.match(line)
        if m1 and i + 1 < len(lines):
            m2 = _DEV_LINE2_RE.match(lines[i + 1])
            if m2:
                npu = int(m1.group("npu"))
                health_str = m1.group("health").strip()
                rest1 = m1.group("rest").split()
                power = _parse_float(rest1[0]) if len(rest1) > 0 else 0.0
                temp = _parse_float(rest1[1]) if len(rest1) > 1 else 0.0

                rest2 = m2.group("rest")
                aicore_m = _NUMBER_RE.search(rest2)
                util = float(aicore_m.group(0)) if aicore_m else 0.0
                usages = _USAGE_RE.findall(rest2)
                mem_used = mem_total = 0
                if len(usages) >= 2:
                    (m_used, m_total), (h_used, h_total) = usages[0], usages[1]
                    if int(h_total) > 0:
                        mem_used, mem_total = int(h_used), int(h_total)
                    else:
                        mem_used, mem_total = int(m_used), int(m_total)
                elif len(usages) == 1:
                    mem_used, mem_total = int(usages[0][0]), int(usages[0][1])

                devices[npu] = DeviceSample(
                    vendor="ascend",
                    index=npu,
                    model=m1.group("name"),
                    util_percent=util,
                    mem_used_bytes=mem_used * MB,
                    mem_total_bytes=mem_total * MB,
                    temp_celsius=temp,
                    power_watts=power,
                    ecc_errors_total=None,
                    health=HEALTH_CODES.get(health_str, HEALTH_UNKNOWN),
                )
                i += 2
                continue
        i += 1

    return [devices[k] for k in sorted(devices)]


class AscendCollector:
    name = "ascend"

    def __init__(self, command: str = "npu-smi", timeout: float = 10.0) -> None:
        self._command = command
        self._timeout = timeout

    def available(self) -> bool:
        if shutil.which(self._command) is None:
            log.warning("npu-smi not found in PATH; Ascend collector disabled")
            return False
        return True

    def collect(self) -> list[DeviceSample]:
        proc = subprocess.run(
            [self._command, "info"],
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{self._command} info exited with {proc.returncode}: {proc.stderr.strip()}"
            )
        return parse_npu_smi_info(proc.stdout)
