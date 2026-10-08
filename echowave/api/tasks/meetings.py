"""Meeting jobs (launch stream `meetings`). See services/meetings/."""


async def transcribe_meeting_segment(
    _ctx, segment_id: int, organization_id: int
) -> None:
    """One live-capture segment, so the transcript fills in during the
    meeting. A no-op for a segment another job already took."""
    from api.db import db_client
    from api.services.meetings import transcription

    language = await db_client.meeting_language_for_segment(
        int(segment_id), organization_id=int(organization_id)
    )
    if language is None:
        return
    await transcription.transcribe_segment(
        int(segment_id), int(organization_id), language
    )


async def finish_meeting(_ctx, meeting_id: int, organization_id: int) -> None:
    """After Stop, an upload or pasted notes: transcribe what is left, decide
    the state honestly, and read it."""
    from api.services.meetings import processing

    await processing.finish(int(meeting_id), int(organization_id))
