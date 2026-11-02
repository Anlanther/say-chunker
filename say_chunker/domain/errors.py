"""User-facing exception hierarchy.

Adapters raise these errors after translating low-level failures (subprocess,
ffmpeg, PDF parsing) so the CLI can emit actionable messages.
"""

from __future__ import annotations


class TTSAppError(Exception):
    """Base class for expected, user-actionable failures."""


class MissingDependencyError(TTSAppError):
    """A required external binary is missing or unusable."""


class InvalidVoiceError(TTSAppError):
    def __init__(self, voice: str, available: list[str]) -> None:
        self.voice = voice
        self.available = available
        listing = "\n".join(f"  - {name}" for name in available) or "  (none found)"
        super().__init__(
            f"Voice {voice!r} is not installed.\n"
            f"Available voices:\n{listing}\n"
            "Run `tts voices` for the full list."
        )


class UnsupportedFormatError(TTSAppError):
    """The input extension is unknown and no --format override was supplied."""


class ExtractionError(TTSAppError):
    """The source document could not be read or contained no speakable text."""


class PDFPasswordRequiredError(ExtractionError):
    """The PDF is encrypted and no password was supplied."""


class PDFPasswordError(ExtractionError):
    """The supplied PDF password was rejected."""


class EmptyDocumentError(ExtractionError):
    """Extraction/splitting produced no speakable text."""


class OutputNotWritableError(TTSAppError):
    """The output file or state directory cannot be written."""


class JobNotFoundError(TTSAppError):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(
            f"No job found with id {job_id!r}. Run `tts list-jobs` to see available jobs."
        )


class JobStateError(TTSAppError):
    """A persisted job file is missing required data or is corrupt."""


class JobConfigMismatchError(TTSAppError):
    """The current configuration does not match the persisted job plan."""


class ConfigurationError(TTSAppError):
    """A config value or CLI combination is invalid."""


# ---------------------------------------------------------------------------
# TTS engine
# ---------------------------------------------------------------------------


class TTSEngineError(TTSAppError):
    """Base class for synthesis failures (retryable by the orchestrator)."""


class SynthesisError(TTSEngineError):
    """The engine exited unsuccessfully or produced no output."""


class SynthesisTimeoutError(TTSEngineError):
    """A single chunk exceeded the configured subprocess timeout."""


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


class MergeError(TTSAppError):
    """ffmpeg failed to produce the final MP3."""


class CommandError(TTSAppError):
    """A subprocess helper failure that has not been translated by an adapter."""

    def __init__(self, message: str, *, command: tuple[str, ...], stderr: str = "") -> None:
        self.command = command
        self.stderr = stderr
        detail = f"\n{stderr.strip()}" if stderr.strip() else ""
        super().__init__(f"{message}{detail}")


class CommandStartError(CommandError):
    """The executable could not be started."""


class CommandTimeoutError(CommandError):
    """The command exceeded its timeout and was killed."""


class CommandFailedError(CommandError):
    """The command exited non-zero."""
