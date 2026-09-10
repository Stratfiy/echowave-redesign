"""The acknowledgement an erasure request gets.

Erasure is irreversible and the person asking for it is usually asking on
behalf of somebody else who complained. A request that returns a JSON
count and nothing else leaves no record in anyone's inbox that it was
done; this puts one there, and under the bell, so the account can show a
regulator or the complainant the date.
"""

from __future__ import annotations

from api.services.messaging.announce import Notice

KIND = "erasure_completed"


def completed(
    *, request_id: int, number_hint: str, calls_erased: int, files_deleted: int
) -> Notice:
    return Notice(
        subject="Erasure request completed",
        body=(
            f"The data for {number_hint} has been erased from this account: "
            f"{calls_erased} call{'s' if calls_erased != 1 else ''} and "
            f"{files_deleted} recording or transcript file"
            f"{'s' if files_deleted != 1 else ''}.\n\n"
            "This cannot be undone. The erasure log under Compliance keeps the "
            "record of who asked and when."
        ),
        dedupe_key=f"erasure:{request_id}",
        link="/privacy",
    )


def mask_number(number: str) -> str:
    """The last four digits only. The point of erasure is that the number
    stops being written down; a confirmation carrying it in full would be
    one more copy."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    return f"a number ending {digits[-4:]}" if len(digits) >= 4 else "the number"
