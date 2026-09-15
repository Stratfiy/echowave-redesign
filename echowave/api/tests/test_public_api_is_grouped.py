"""Every public operation has a home in the reference.

The parent router used to tag everything ``main``, so a router that never
declared a tag was indistinguishable from one that had, and the public
document listed 388 operations under one heading. Now a router names its
own tag and the surface groups the tags into the sections a customer reads.
Both halves are guarded here: an operation with no tag, and a tag that is in
no group, each fail with the operations named, so a new router is placed
rather than lost (KAN-84).
"""

from __future__ import annotations

from api.app import app
from api.services import openapi_surface


def _grouped() -> set[str]:
    return {tag for _, members in openapi_surface.TAG_GROUPS for tag in members}


class TestEveryPublicOperationIsPlaced:
    def test_no_operation_is_untagged(self):
        tags = openapi_surface.public_tags(openapi_surface.public_spec(app))
        assert tags.get("", []) == []

    def test_nothing_is_tagged_main_any_more(self):
        tags = openapi_surface.public_tags(openapi_surface.public_spec(app))
        assert "main" not in tags

    def test_every_public_tag_is_in_a_group(self):
        tags = openapi_surface.public_tags(openapi_surface.public_spec(app))
        homeless = {tag: ops for tag, ops in tags.items() if tag not in _grouped()}
        assert homeless == {}

    def test_every_grouped_tag_is_used(self):
        """A group naming a tag no router carries is a rename that was not
        finished."""
        tags = openapi_surface.public_tags(openapi_surface.public_spec(app))
        unused = sorted(_grouped() - set(tags))
        assert unused == []

    def test_no_tag_is_in_two_groups(self):
        seen: dict[str, str] = {}
        for name, members in openapi_surface.TAG_GROUPS:
            for tag in members:
                assert tag not in seen, f"{tag} is in both {seen[tag]} and {name}"
                seen[tag] = name

    def test_the_document_carries_the_groups(self):
        spec = openapi_surface.public_spec(app)
        names = [group["name"] for group in spec["x-tagGroups"]]
        assert names == [name for name, _ in openapi_surface.TAG_GROUPS]

    def test_no_group_names_a_staff_tag(self):
        for _, members in openapi_surface.TAG_GROUPS:
            for tag in members:
                assert not tag.startswith(openapi_surface.INTERNAL_TAG_PREFIXES)
