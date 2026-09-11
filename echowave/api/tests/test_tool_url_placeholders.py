"""Arguments that belong in a URL's path rather than beside it.

Most REST APIs address a thing by path -- Shopify's
`/orders/{order_id}/fulfillments.json` and nearly every other `/resource/{id}`.
Before this, an HTTP tool could only reach endpoints whose arguments fit in a
query string or a body, and a URL written the natural way was sent with a
literal brace in it.

The last class here is the one that earns its keep: it checks every entry in
the tool library against the mechanism, because a template that is wrong fails
on a live call rather than at setup. It would have caught the two broken
Shopify templates in this commit's first draft.
"""

import re

import pytest

from api.services.integrations.tool_library import _LIBRARY
from api.services.workflow.tools.custom_tool import fill_url_placeholders

BASE = "https://shop.myshopify.com/admin/api/2026-07"


class TestFillingThePath:
    def test_a_url_without_placeholders_is_untouched(self):
        url, rest, err = fill_url_placeholders(f"{BASE}/orders.json", {"name": "1001"})
        assert url == f"{BASE}/orders.json"
        assert rest == {"name": "1001"}
        assert err is None

    def test_a_placeholder_is_filled(self):
        url, _, err = fill_url_placeholders(
            f"{BASE}/orders/{{order_id}}.json", {"order_id": 5521}
        )
        assert url == f"{BASE}/orders/5521.json"
        assert err is None

    def test_a_filled_argument_is_not_sent_again(self):
        """Otherwise the id goes in the path *and* in the query string, and
        the second one is what some APIs read."""
        _, rest, _ = fill_url_placeholders(
            f"{BASE}/orders/{{order_id}}/fulfillments.json",
            {"order_id": 5521, "fields": "tracking_number"},
        )
        assert rest == {"fields": "tracking_number"}

    def test_several_placeholders(self):
        url, rest, err = fill_url_placeholders(
            "https://x/a/{one}/b/{two}", {"one": "1", "two": "2", "three": "3"}
        )
        assert url == "https://x/a/1/b/2"
        assert rest == {"three": "3"}
        assert err is None


class TestWhenItCannotFill:
    def test_a_missing_argument_is_reported_not_requested(self):
        """An unfilled brace reaches somebody else's server and 404s in a way
        nobody can read, so the model is told instead."""
        url, rest, err = fill_url_placeholders("https://x/o/{order_id}.json", {})
        assert err and "order_id" in err
        assert url == "https://x/o/{order_id}.json"
        assert rest == {}

    def test_a_null_argument_counts_as_missing(self):
        _, _, err = fill_url_placeholders(
            "https://x/o/{order_id}.json", {"order_id": None}
        )
        assert err is not None

    def test_nothing_is_half_filled(self):
        """The first failure returns the original URL, not one with some
        placeholders replaced and others left as braces."""
        url, _, err = fill_url_placeholders("https://x/{a}/{b}", {"b": "2"})
        assert err is not None
        assert url == "https://x/{a}/{b}"


class TestItCannotBeUsedToGoSomewhereElse:
    def test_a_traversal_becomes_one_nonsense_segment(self):
        url, _, _ = fill_url_placeholders(
            "https://x/o/{id}.json", {"id": "../../admin"}
        )
        assert ".." not in url.replace("%2F", "/").split("/o/")[0]
        assert url == "https://x/o/..%2F..%2Fadmin.json"

    def test_a_slash_cannot_add_a_path_segment(self):
        url, _, _ = fill_url_placeholders("https://x/o/{id}", {"id": "1/2"})
        assert url == "https://x/o/1%2F2"

    def test_a_query_string_cannot_be_smuggled_in(self):
        url, _, _ = fill_url_placeholders("https://x/o/{id}", {"id": "1?admin=true"})
        assert "?" not in url

    def test_braces_that_are_not_names_are_left_alone(self):
        for url in ("https://x/{}", "https://x/{1}", "https://x/{a-b}"):
            assert fill_url_placeholders(url, {})[0] == url


class TestEveryLibraryTemplateMatchesTheMechanism:
    """The guard. A catalogue entry is data, so nothing type-checks it."""

    @pytest.mark.parametrize("entry", _LIBRARY, ids=lambda e: e.key)
    def test_every_url_placeholder_is_a_declared_parameter(self, entry):
        placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", entry.url))
        declared = {p.name for p in entry.parameters}
        assert placeholders <= declared, (
            f"{entry.key} has {placeholders - declared} in its URL with no "
            f"matching parameter, so the call would be made with a literal brace"
        )

    @pytest.mark.parametrize("entry", _LIBRARY, ids=lambda e: e.key)
    def test_a_path_parameter_is_required(self, entry):
        """An optional argument that the URL cannot do without is a tool that
        fails at the vendor instead of at the model."""
        placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", entry.url))
        for parameter in entry.parameters:
            if parameter.name in placeholders:
                assert parameter.required, (
                    f"{entry.key}: {parameter.name} is in the path"
                )

    @pytest.mark.parametrize("entry", _LIBRARY, ids=lambda e: e.key)
    def test_a_get_does_not_repeat_a_parameter_in_its_query_string(self, entry):
        """A GET sends its arguments as query parameters, so a URL that already
        names one sends it twice -- once hardcoded, once real."""
        if entry.method not in ("GET", "DELETE") or "?" not in entry.url:
            return
        hardcoded = {
            pair.split("=")[0] for pair in entry.url.split("?", 1)[1].split("&") if pair
        }
        for parameter in entry.parameters:
            assert parameter.name not in hardcoded, (
                f"{entry.key}: {parameter.name} is both hardcoded in the query "
                f"string and sent as an argument"
            )
