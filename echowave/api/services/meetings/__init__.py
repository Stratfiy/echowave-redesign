"""Meeting mode (launch stream `meetings`; handoff 23, 31.6; screens 11-12).

A person records a meeting on this device's microphone, uploads a recording,
or pastes notes -- after confirming they have the participants' permission
for any audio. Sarvam turns the audio into words in the language that was
spoken; the transcript is read for a summary, decisions and suggested
follow-ups; and each follow-up becomes real only through its own action
card (services/workflow/actions.py), confirmed by the person who captured
the meeting.

Modules:

* ``records``       -- create, capture moves (segments, pauses, gaps, stop),
                       the record as the screens read it, rename, correct,
                       export, delete.
* ``transcription`` -- Sarvam, one short segment at a time; splitting an
                       upload; what is set up and what is not.
* ``reading``       -- the summary, decisions and suggested actions, each tied
                       to its source excerpt.
* ``processing``    -- what the worker does after Stop.
* ``follow_ups``    -- a suggested action to an action card, and the card's
                       effect (one task) when it runs.

See MEETINGS.md at the repository's ``echowave/`` root.
"""

from __future__ import annotations

from api.services import features

FLAG = "meeting_capture"

#: The prefix of the hidden conversation a meeting's action cards are written
#: to. Not a Chat thread anybody opens: the cards are read from the meeting
#: record, and the timeline refuses to list a thread with this prefix, so a
#: meeting's follow-ups never appear in a shared conversation.
THREAD_PREFIX = "mtg-"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def thread_for(public_id: str) -> str:
    return f"{THREAD_PREFIX}{public_id}"[:36]


def is_meeting_thread(thread_id: str | None) -> bool:
    return bool(thread_id) and str(thread_id).startswith(THREAD_PREFIX)
