"""The tenancy boundary, which is one string and therefore worth this much test.

Graphiti accepts an episode with no ``group_id`` and writes it to the default
partition, where the next organization's search can read it. There is no error
and no log line. So every way a bad organization id could reach the graph is a
test here, and all of them end in a raise.
"""

from __future__ import annotations

import pytest

from api.services.knowledge_graph.scoping import (
    GraphScopeError,
    group_id_for_organization,
    group_ids_for_search,
)


class TestAGoodOrganization:
    def test_it_is_prefixed_rather_than_a_bare_number(self):
        assert group_id_for_organization(42) == "org:42"

    def test_two_organizations_never_share_a_partition(self):
        assert group_id_for_organization(1) != group_id_for_organization(2)

    def test_search_reads_one_partition_and_only_one(self):
        assert group_ids_for_search(7) == ["org:7"]


class TestNothingElseGetsThrough:
    """Each of these would otherwise be a cross-tenant read."""

    def test_none_raises_rather_than_defaulting(self):
        with pytest.raises(GraphScopeError):
            group_id_for_organization(None)

    def test_zero_raises(self):
        """Zero is what an unset integer column looks like."""
        with pytest.raises(GraphScopeError):
            group_id_for_organization(0)

    def test_a_negative_id_raises(self):
        with pytest.raises(GraphScopeError):
            group_id_for_organization(-1)

    def test_a_string_id_raises(self):
        """A request parameter that was never coerced."""
        with pytest.raises(GraphScopeError):
            group_id_for_organization("3")  # type: ignore[arg-type]

    def test_true_is_not_organization_one(self):
        """bool is an int in Python, and True would silently mean org 1."""
        with pytest.raises(GraphScopeError):
            group_id_for_organization(True)  # type: ignore[arg-type]

    def test_search_refuses_the_same_things_writing_does(self):
        with pytest.raises(GraphScopeError):
            group_ids_for_search(None)
