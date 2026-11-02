"""Application-specific exception hierarchy."""

from __future__ import annotations


class TTSAppError(Exception):
    """Base class for all user-facing errors raised by the app."""


class MissingDependencyError(TTSAppError):
    """A required external binary (``say``, ``ffmpeg``, ``ffprobe``) is absent."""


class InvalidVoiceError(TTSAppError):
    """The requested ``say`` voice is not installed."""


class ExtractionError(TTSAppError):
    """A document could not be read or extracted."""


class SynthesisError(TTSAppError):
    """A ``say`` subprocess failed or timed out."""


class MergeError(TTSAppError):
    """The ffmpeg merge step failed."""


class ConfigMismatchError(TTSAppError):
    """Resume configuration does not match the stored job."""


class JobStateMismatchError(TTSAppError):
    """Stored job state no longer matches the re-derived input."""


class JobNotFoundError(TTSAppError):
    """No job exists with the requested id."""


class OutputNotWritableError(TTSAppError):
    """The requested output path cannot be written to."""


class SubprocessError(TTSAppError):
    """A subprocess failed, timed out, or could not be started."""
