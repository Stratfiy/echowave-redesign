"""Personal memory per member (PRD v2 MEM-1, KAN-197).

What a member tells Decibyl in their own conversation is theirs. It is stored
against their user id in the record and written to their own partition of the
graph, and only their own recall, search and summaries read it back. Everyone
reads the workspace's memory; nobody reads a colleague's.

Two questions, answered here and nowhere else:

* ``owner()`` -- whose is a fact being written right now? The member the turn
  runs as (``acting.acting_as``, set by the Decibyl turn), or None for the
  workspace. A call, a routine, a document arriving on a channel and a screen
  that writes the workspace record all run with no member, so they stay the
  workspace's.
* ``viewer()`` -- whose personal memory may a read include? The same member,
  or None, in which case no personal memory is returned at all.

Off (the default) both answer None, and every read and write is exactly what
it was before this module existed.
"""

from __future__ import annotations

from typing import Optional

from api.services import acting, features

FLAG = "personal_memory"


def enabled() -> bool:
    return features.is_on(FLAG)


def owner() -> Optional[int]:
    """The member a write in this turn belongs to, or None for the workspace."""
    if not enabled():
        return None
    return acting.acting_user()


def viewer() -> Optional[int]:
    """The member whose personal memory a read in this turn may include."""
    if not enabled():
        return None
    return acting.acting_user()


__all__ = ["FLAG", "enabled", "owner", "viewer"]
