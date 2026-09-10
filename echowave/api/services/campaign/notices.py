"""What an account is told about a campaign, once it has something to say.

A campaign runs for hours after the person who started it has closed the
tab. Its ending — how many were reached, how many were not — went nowhere
but the campaign page, which nobody has open by then. One notice, by mail
and under the bell, when it completes.
"""

from __future__ import annotations

from api.services.messaging.announce import Notice

KIND = "campaign_completed"


def completed(
    *,
    campaign_id: int,
    name: str,
    total_rows: int,
    processed_rows: int,
    failed_rows: int,
    app_url: str,
) -> Notice:
    reached = max(processed_rows - failed_rows, 0)
    base = (app_url or "").rstrip("/")
    return Notice(
        subject=f"Campaign “{name}” has finished",
        body=(
            f"{reached} of {total_rows} calls connected"
            + (f", {failed_rows} did not" if failed_rows else "")
            + ".\n\n"
            f"Recordings, transcripts and outcomes are on the campaign page:\n"
            f"  {base}/campaigns/{campaign_id}\n\n"
            "Anyone who could not be reached can be called again from there."
        ),
        dedupe_key=f"completed:{campaign_id}",
        link=f"/campaigns/{campaign_id}",
    )
