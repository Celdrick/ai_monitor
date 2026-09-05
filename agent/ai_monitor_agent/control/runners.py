"""CLI runners for py-spy, nsys, msprof (and fake-debug stubs)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tarfile

from ..services import ServiceInfo
from .artifacts import register_file
from .tasks import Task

log = logging.getLogger(__name__)


def clamp_duration(value: int | None) -> int:
    n = 15 if value is None else int(value)
    return max(5, min(60, n))


def which(name: str) -> str | None:
    return shutil.which(name)


async def _run_cmd(task: Task, args: list[str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    task.proc = proc
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"command timed out: {' '.join(args)}")
    finally:
        task.proc = None
    text = (stdout or b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, text


def _need_pid(svc: ServiceInfo) -> int:
    if svc.pid is None:
        raise RuntimeError("service has no pid; cannot attach")
    return svc.pid


async def run_pyspy(task: Task, svc: ServiceInfo, mode: str, duration: int, dest_dir: str, fake: bool) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    if fake:
        if mode == "dump":
            path = os.path.join(dest_dir, f"{svc.name}-pyspy-dump.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"Thread 0x1 (python):\n#0  fake_dump ({svc.name})\n")
            register_file(task, path, "pyspy_dump")
        else:
            path = os.path.join(dest_dir, f"{svc.name}-pyspy-record.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"$schema":"https://www.speedscope.app/file-format-schema.json","profiles":[]}\n')
            register_file(task, path, "pyspy_record")
        return

    exe = which("py-spy")
    if not exe:
        raise RuntimeError("py-spy not found in PATH")
    pid = _need_pid(svc)
    if mode == "dump":
        path = os.path.join(dest_dir, f"{svc.name}-pyspy-dump.txt")
        code, out = await _run_cmd(task, [exe, "dump", "--pid", str(pid)], timeout=30)
        if code != 0:
            raise RuntimeError(_ptrace_hint(out) or f"py-spy dump failed: {out[-400:]}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out)
        register_file(task, path, "pyspy_dump")
        return

    path = os.path.join(dest_dir, f"{svc.name}-pyspy-record.json")
    code, out = await _run_cmd(
        task,
        [exe, "record", "--pid", str(pid), "--duration", str(duration), "--format", "speedscope", "-o", path],
        timeout=duration + 30,
    )
    if code != 0:
        raise RuntimeError(_ptrace_hint(out) or f"py-spy record failed: {out[-400:]}")
    if not os.path.isfile(path):
        raise RuntimeError("py-spy record produced no file")
    register_file(task, path, "pyspy_record")


async def run_nsys(task: Task, svc: ServiceInfo, duration: int, dest_dir: str, fake: bool) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.join(dest_dir, f"{svc.name}-nsys")
    if fake:
        path = base + ".nsys-rep"
        with open(path, "wb") as fh:
            fh.write(b"FAKE-NSYS\n")
        register_file(task, path, "nsys_rep")
        return
    exe = which("nsys")
    if not exe:
        raise RuntimeError("nsys not found in PATH")
    pid = _need_pid(svc)
    code, out = await _run_cmd(
        task,
        [exe, "profile", f"--duration={duration}", f"--pid={pid}", "-o", base, "--force-overwrite=true"],
        timeout=duration + 60,
    )
    if code != 0 and ("Unknown option" in out or "--pid" in out.lower()):
        session = f"aim-{task.id[:8]}"
        code, out = await _run_cmd(task, [exe, "start", f"--session={session}"], timeout=30)
        if code != 0:
            raise RuntimeError(f"nsys start failed: {out[-400:]}")
        try:
            await asyncio.sleep(duration)
        finally:
            code, out = await _run_cmd(
                task, [exe, "stop", f"--session={session}", "-o", base], timeout=120
            )
        if code != 0:
            raise RuntimeError(f"nsys stop failed: {out[-400:]}")
    elif code != 0:
        raise RuntimeError(f"nsys profile failed: {out[-400:]}")
    path = base + ".nsys-rep"
    if not os.path.isfile(path):
        # nsys sometimes appends nothing extra
        if os.path.isfile(base):
            path = base
        else:
            raise RuntimeError("nsys produced no .nsys-rep")
    register_file(task, path, "nsys_rep")


async def run_msprof(task: Task, svc: ServiceInfo, duration: int, dest_dir: str, fake: bool) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    out_dir = os.path.join(dest_dir, f"{svc.name}-msprof")
    os.makedirs(out_dir, exist_ok=True)
    if fake:
        with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"fake msprof for {svc.name} duration={duration}\n")
    else:
        exe = which("msprof")
        if not exe:
            raise RuntimeError("msprof not found in PATH")
        args = [exe, f"--output={out_dir}", f"--duration={duration}"]
        if svc.pid is not None:
            args.append(f"--pid={svc.pid}")
        code, out = await _run_cmd(task, args, timeout=duration + 60)
        if code != 0 and "--pid" in out:
            code, out = await _run_cmd(
                task, [exe, f"--output={out_dir}", f"--duration={duration}"], timeout=duration + 60
            )
        if code != 0:
            raise RuntimeError(f"msprof failed: {out[-400:]}")
    tar_path = out_dir + ".tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(out_dir, arcname=os.path.basename(out_dir))
    register_file(task, tar_path, "msprof")


def _ptrace_hint(output: str) -> str | None:
    low = output.lower()
    if "ptrace" in low or "permission" in low or "operation not permitted" in low:
        return (
            "py-spy could not attach (ptrace). Grant CAP_SYS_PTRACE, run with "
            "--pid=host, or set kernel.yama.ptrace_scope=0. " + output[-200:]
        )
    return None
