"""Which member of the workspace the current turn runs as.

Set once at the top of a Decibyl turn (``decibyl.answer``) and read by
whatever needs to know whose the turn is, several calls further down:
the connected-tool executor picks that member's own mailbox (WS-1), and
memory writes and reads are scoped to them (MEM-1).

A context variable rather than an argument, because the readers sit six
calls below the one place that knows who asked, and threading an id
through every layer is how one of them ends up forgotten -- and a
forgotten id here means one colleague's mail or memory reaching another,
with nothing failing.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_acting: ContextVar[Optional[int]] = ContextVar("acting_member", default=None)


def valid_member(user_id: Any) -> Optional[int]:
    """A real user id, or None. A bool, a zero or a string is not one."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    return user_id


@contextmanager
def acting_as(user_id: Optional[int]) -> Iterator[None]:
    """Everything inside runs as this member."""
    token = _acting.set(valid_member(user_id))
    try:
        yield
    finally:
        _acting.reset(token)


def acting_user() -> Optional[int]:
    """The member the current turn runs as, when one was set."""
    return _acting.get()


__all__ = ["acting_as", "acting_user", "valid_member"]
