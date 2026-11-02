from __future__ import annotations

import time
from pathlib import Path

import pytest

from say_chunker.domain.errors import (
    CommandFailedError,
    CommandStartError,
    CommandTimeoutError,
)
from say_chunker.utils.subprocess_helpers import run_command


def test_run_command_success() -> None:
    result = run_command(["echo", "hello"], timeout=5.0)
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_command_nonzero_raises_with_stderr() -> None:
    with pytest.raises(CommandFailedError) as error:
        run_command(["sh", "-c", "printf boom >&2; exit 7"], timeout=5.0)
    assert "exit code 7" in str(error.value)
    assert "boom" in str(error.value)
    assert error.value.command[:2] == ("sh", "-c")


def test_run_command_check_false_returns_result() -> None:
    result = run_command(["sh", "-c", "exit 4"], timeout=5.0, check=False)
    assert result.returncode == 4


def test_run_command_timeout_kills_child() -> None:
    started = time.monotonic()
    with pytest.raises(CommandTimeoutError, match="timed out"):
        run_command(["sh", "-c", "sleep 10"], timeout=0.2, new_session=True)
    assert time.monotonic() - started < 3.0


def test_run_command_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(CommandStartError, match="Could not start"):
        run_command([str(tmp_path / "does-not-exist")], timeout=5.0)
