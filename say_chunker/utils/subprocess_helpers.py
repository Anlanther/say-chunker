"""Subprocess execution with timeouts, process-group cleanup, and structured errors."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Sequence

from say_chunker.domain.errors import CommandFailedError, CommandStartError, CommandTimeoutError


def run_command(
    args: Sequence[str],
    *,
    timeout: float,
    check: bool = True,
    new_session: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing text output.

    When ``new_session`` is true the child becomes a session leader. On POSIX
    this isolates it from the terminal's Ctrl-C (SIGINT) group, which lets the
    orchestrator finish the current ``say`` chunk before pausing. Timeouts kill
    the whole group so no orphan synthesis process survives.

    Raises:
        CommandStartError: the executable could not be started.
        CommandTimeoutError: the command exceeded ``timeout`` and was killed.
        CommandFailedError: the command exited non-zero and ``check`` is true.
    """
    command = tuple(str(part) for part in args)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=new_session,
        )
    except OSError as exc:
        raise CommandStartError(f"Could not start {command[0]!r}: {exc}", command=command) from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate(process, new_session=new_session)
        raise CommandTimeoutError(
            f"Command timed out after {timeout:g}s: {command[0]}",
            command=command,
        ) from exc

    if check and process.returncode != 0:
        raise CommandFailedError(
            f"Command failed with exit code {process.returncode}: {command[0]}",
            command=command,
            stderr=stderr,
        )

    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _terminate(process: subprocess.Popen[str], *, new_session: bool) -> None:
    try:
        if new_session and os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass

    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=1)
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
