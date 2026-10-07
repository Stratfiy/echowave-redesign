"""Who may see a row a person owns in a workspace.

``private`` is the owner alone; ``workspace`` is every member of that
workspace (teams share agents, knowledge and learnings, founder request).
The filter is in the query, never applied to rows already read, and the
organisation is always part of it, so a row shared in one workspace is
never visible from another. In a personal space the two are the same
person, which is the point of the personal space.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_

PRIVATE = "private"
WORKSPACE = "workspace"
VISIBILITIES = (PRIVATE, WORKSPACE)


class Invalid(ValueError):
    """Something the person can fix; ``str(exc)`` is safe to show."""


def clean(value: Any) -> str:
    text = str(value or PRIVATE).strip().lower()
    if text not in VISIBILITIES:
        raise Invalid("Choose private or workspace.")
    return text


def visible_to(model: Any, *, organization_id: int, user_id: int):
    """The WHERE clause: this workspace, and the person's own or shared."""
    return (
        model.organization_id == organization_id,
        or_(model.owner_user_id == user_id, model.visibility == WORKSPACE),
    )


def owned_by(model: Any, *, organization_id: int, user_id: int):
    """Only the owner changes a row; a teammate who can see it cannot."""
    return (model.organization_id == organization_id, model.owner_user_id == user_id)
