"""The supervisor's voice, as a frame the call's output plays.

A plain ``OutputAudioRawFrame`` subclass, deliberately not a
``TTSAudioRawFrame`` or ``SpeechOutputAudioRawFrame``: the output transport
treats only those two as the bot speaking. So the supervisor's voice never
starts a bot turn, never trips "bot speaking" interruption logic and never
holds the caller's microphone shut -- it is simply played, and recorded with
the call (the audio buffer sits after the output transport).

Its own class also lets the listen tap tell it apart from the agent, so the
person speaking does not hear themselves back.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipecat.frames.frames import OutputAudioRawFrame


@dataclass
class SupervisorAudioFrame(OutputAudioRawFrame):
    """A slice of the supervisor's voice on its way to the caller."""
