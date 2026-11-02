"""Logging configuration for library and CLI code."""

from __future__ import annotations

import logging
from pathlib import Path

from say_chunker.domain.errors import OutputNotWritableError

LOGGER_NAME = "say_chunker"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    log_file: Path | None = None,
    *,
    verbose: bool = False,
    console_level: int | None = None,
) -> None:
    """Install a console handler and, optionally, a per-job file handler.

    Library code logs through ``say_chunker.*`` loggers and never prints. The CLI is
    responsible for calling this once per invocation.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    level = console_level
    if level is None:
        level = logging.INFO if verbose else logging.WARNING
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as exc:
            raise OutputNotWritableError(f"Cannot create log file {log_file}: {exc}") from exc
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
