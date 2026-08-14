"""Batch transcription of an uploaded audio file.

Mirrors ``gen_ai/embedding``: a base interface, one implementation per vendor,
and a factory that resolves the organization's own configuration. MPS remains
one option — the right one for the managed tier — rather than the only one.
"""

from .base import (
    BaseTranscriptionService,
    Transcription,
    TranscriptionError,
    TranscriptionNotSupportedError,
)
from .factory import SUPPORTED, build_transcription_service

__all__ = [
    "SUPPORTED",
    "BaseTranscriptionService",
    "Transcription",
    "TranscriptionError",
    "TranscriptionNotSupportedError",
    "build_transcription_service",
]
