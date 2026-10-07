"""People: a person's own contacts, synced and kept current (PEOPLE.md).

One switch, ``people``, off by default and honouring per-workspace overrides
from the staff console. Off, the routes are 404s, Decibyl is not handed the
lookup, nothing is recorded from calls, mail or meetings, and the sweep that
writes briefs does nothing.

The whole package keeps one rule: a contact belongs to the person who has it
(``owner_user_id``) and every query names that person. Nothing here takes a
user id from a request body; the caller is the owner, always.
"""

from __future__ import annotations

from api.services import features

FLAG = "people"


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)
