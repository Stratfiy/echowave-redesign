"""Which graph partition an organization's knowledge lives in.

Graphiti stores everything in one graph and separates tenants with a
``group_id``: episodes are written with one, searches are filtered to a list of
them. That single string is the whole tenancy boundary, so this module exists
to make it impossible to get wrong from anywhere else in the codebase.

**The failure mode is silent and it is the worst one we have.** ``group_id`` is
optional in Graphiti's API. An episode written without one does not error — it
lands in the default partition, where it is visible to any search that does not
filter. One dental clinic's patient names would be readable by the next
organization to ask a vaguely similar question, and nothing in the logs would
say so. So a missing or unusable organization id raises here rather than
falling back to a default, and there is no code path in this package that calls
Graphiti without going through these functions first.

The format is ``org:<id>`` rather than the bare integer because a bare "1" is
the kind of value that gets reused by accident for something that is not an
organization. The prefix makes a wrong group id visibly wrong.
"""

from __future__ import annotations

#: Prefix on every organization partition. Changing this orphans every episode
#: already written, so it is a migration, not an edit.
ORGANIZATION_PREFIX = "org"


class GraphScopeError(ValueError):
    """An organization id that cannot be turned into a safe partition."""


def group_id_for_organization(organization_id: int | None) -> str:
    """The partition an organization's episodes are written to.

    Raises rather than returning a default: see the module docstring. A caller
    that does not know the organization must not write to the graph at all.
    """
    if organization_id is None:
        raise GraphScopeError(
            "Refusing to write to the knowledge graph without an organization: "
            "an episode with no partition is readable by every other tenant."
        )
    if isinstance(organization_id, bool) or not isinstance(organization_id, int):
        raise GraphScopeError(
            f"Organization id must be an integer, got {type(organization_id).__name__}"
        )
    if organization_id <= 0:
        raise GraphScopeError(
            f"Organization id must be positive, got {organization_id}"
        )
    return f"{ORGANIZATION_PREFIX}:{organization_id}"


def group_ids_for_search(organization_id: int | None) -> list[str]:
    """The partitions a search for this organization may read.

    One, always. The list is Graphiti's shape, not an invitation to widen it:
    searching more than one organization's partition is never a thing this
    product does, and a helper that made it easy would eventually be used.
    """
    return [group_id_for_organization(organization_id)]
