"""Transcribing an uploaded audio file, as opposed to a live call.

The live path is pipecat's streaming STT and is not this. This is the batch
case: somebody uploads a recording on the recordings screen and wants the words
back. Same vendors, different endpoint, and until now the only implementation
was a call to the Model Proxy Service with no fallback — so on a deployment
that could not reach MPS, the button did nothing and said "Failed to transcribe
audio".

The interface is deliberately narrow. A batch transcription has one job, and
every vendor's prerecorded endpoint returns some superset of the same three
facts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class TranscriptionError(Exception):
    """A failure the person who uploaded the file can be told about.

    ``user_message`` is what the screen shows. As with knowledge base
    ingestion, the rule is that it names what to do next or says plainly that
    the problem is ours — never a vendor's status code or our hostname.
    """

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


class TranscriptionNotSupportedError(TranscriptionError):
    """This provider has no batch endpoint we implement.

    Distinct from a failure, because the remedy is different: the account's
    live-call STT is working fine and only this screen is affected, so the fix
    is to pick a different provider for file transcription rather than to
    investigate an outage.
    """


@dataclass(frozen=True)
class Transcription:
    """What every caller of this package needs, and nothing else.

    ``transcript`` is the only field the recordings screen reads; the other two
    are here because every vendor returns them and throwing them away would
    mean a second call to get a duration.
    """

    transcript: str
    language: str | None = None
    duration_seconds: float | None = None
    model: str | None = None

    def as_response(self) -> dict:
        """The wire shape the route has always returned.

        Kept identical to what MPS returned so the UI, the generated SDK and
        the docs did not have to change for this — the point of the exercise
        was to remove a dependency, not to renegotiate a contract.
        """
        return {
            "transcript": self.transcript,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "model": self.model,
        }


class BaseTranscriptionService(ABC):
    """One vendor's prerecorded transcription endpoint."""

    @abstractmethod
    def get_model_id(self) -> str:
        """The model this service will use, for the response and the log."""

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: str = "en",
    ) -> Transcription:
        """Turn audio into words.

        Raises :class:`TranscriptionError` for anything a caller should show a
        person. Anything else escaping from here is a bug.
        """
