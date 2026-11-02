"""Small, well-behaved subprocess wrappers with timeout + cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Sequence

from say_chunker.domain.errors import SubprocessError


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_checked(
    args: Sequence[str],
    *,
    timeout_seconds: float,
    description: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output, raising :class:`SubprocessError` on failure.

    The child runs in its own process group so a timeout can cleanly kill the
    entire group rather than orphaning grandchildren.
    """
    try:
        proc = subprocess.Popen(
            [str(a) for a in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            cwd=str(cwd) if cwd is not None else None,
        )
    except OSError as exc:
        raise SubprocessError(f"failed to start {description}: {exc}") from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        proc.communicate()
        raise SubprocessError(f"{description} timed out after {timeout_seconds:.0f}s") from exc

    if proc.returncode != 0:
        detail = (stderr or stdout or "").strip()
        raise SubprocessError(
            f"{description} exited with code {proc.returncode}" + (f": {detail}" if detail else "")
        )

    return subprocess.CompletedProcess(
        args=[str(a) for a in args],
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )
