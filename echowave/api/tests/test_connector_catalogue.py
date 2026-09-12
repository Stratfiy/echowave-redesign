"""Curation: how Composio's 1,540 toolkits are ordered for an operator.

Nothing offerable is dropped. The tests here are about the two claims the
screen makes instead: that a row labelled one-click really can be connected in
one click, and that the rows a business actually asks for are the ones it sees
without scrolling.
"""

import pytest

from api.services.integrations.composio.catalogue import (
    GROUPS,
    SETUP_API_KEY,
    SETUP_NEEDS_APPROVAL,
    SETUP_NO_AUTH,
    SETUP_ONE_CLICK,
    curate,
    search,
)


def _toolkit(slug, name="App", categories=("email",), **kwargs):
    base = {
        "slug": slug,
        "name": name,
        "auth_schemes": ["OAUTH2"],
        "composio_managed_auth_schemes": [],
        "no_auth": False,
        "meta": {
            "description": f"{name} does things",
            "logo": f"https://logos.composio.dev/api/{slug}",
            "tools_count": 12,
            "categories": [{"id": c, "name": c} for c in categories],
        },
    }
    base.update(kwargs)
    return base


class TestSetupClassification:
    def test_a_composio_managed_app_is_one_click(self):
        rows = curate([_toolkit("gmail", composio_managed_auth_schemes=["OAUTH2"])])
        assert rows[0].setup == SETUP_ONE_CLICK

    def test_an_api_key_app_is_offered_and_labelled(self):
        """Shopify and Razorpay both land here. They are usable today -- the
        customer just has to fetch a key -- so hiding them would be wrong."""
        rows = curate([_toolkit("shopify", auth_schemes=["API_KEY", "OAUTH2"])])
        assert rows[0].setup == SETUP_API_KEY

    def test_an_oauth_app_we_have_not_registered_is_still_listed(self):
        """Absent would be the easy choice and the wrong one: a customer who
        wants DocuSign should see that we know it exists. Which of these gets
        asked for is the only honest signal about what to register next."""
        rows = curate([_toolkit("docusign", auth_schemes=["OAUTH2"])])
        assert rows[0].setup == SETUP_NEEDS_APPROVAL

    def test_an_app_needing_nothing_says_so(self):
        rows = curate([_toolkit("weather", no_auth=True, auth_schemes=[])])
        assert rows[0].setup == SETUP_NO_AUTH

    def test_managed_auth_beats_a_bare_auth_scheme_list(self):
        """`auth_schemes` says what the provider supports; only
        `composio_managed_auth_schemes` says Composio carries the OAuth app.
        Reading the first would label every OAuth toolkit one-click, and the
        customer would find out it is not at the worst moment."""
        rows = curate(
            [
                _toolkit(
                    "zoho_books",
                    auth_schemes=["OAUTH2"],
                    composio_managed_auth_schemes=["OAUTH2"],
                )
            ]
        )
        assert rows[0].setup == SETUP_ONE_CLICK


class TestDeduplicationAndShape:
    def test_developer_tooling_is_listed_but_last(self):
        """It is not dropped -- an operator who wants GitHub should be able to
        have GitHub. It just sits below everything a business asks for first,
        and is reached by typing a name rather than by scrolling."""
        rows = curate(
            [
                _toolkit(
                    "hugging_face", name="Hugging Face", categories=("developer tools",)
                ),
                _toolkit("gmail", name="Gmail", categories=("email",)),
            ]
        )
        assert [r.slug for r in rows] == ["gmail", "hugging_face"]
        assert rows[1].group == "Developer"

    def test_the_mcp_flavour_is_dropped_when_the_real_toolkit_is_there(self):
        """Two Box rows differing only by a suffix is a worse screen, not a
        richer one."""
        rows = curate(
            [
                _toolkit("box", name="Box", categories=("documents",)),
                _toolkit("box_mcp", name="Box MCP", categories=("documents",)),
            ]
        )
        assert [r.slug for r in rows] == ["box"]

    def test_an_mcp_only_toolkit_is_kept_because_it_is_the_only_one(self):
        """Cashfree is published by Composio only as `cashfree_payments_mcp`.
        Dropping every `_mcp` slug took a payment gateway a great many Indian
        businesses use off the screen entirely -- the same silent-absence bug
        the category allowlist had, wearing a different hat."""
        rows = curate(
            [
                _toolkit(
                    "cashfree_payments_mcp",
                    name="Cashfree Payments",
                    categories=("payment processing",),
                )
            ]
        )
        assert [r.slug for r in rows] == ["cashfree_payments_mcp"]
        assert rows[0].group == "Money"

    def test_a_deprecated_toolkit_is_dropped(self):
        assert curate([_toolkit("old_app", deprecated=True)]) == []

    def test_a_row_with_no_slug_or_name_cannot_crash_the_screen(self):
        assert curate([{"slug": "x"}, {"name": "y"}, {}, None, "nonsense"]) == []


class TestOrdering:
    def test_rows_of_every_setup_kind_sort_together_by_name(self):
        """Setup kind deliberately does not sort. These three are alphabetical
        whatever it takes to connect them -- see
        TestPopularityOrder.test_setup_never_decides_the_order for why."""
        rows = curate(
            [
                _toolkit("zed", name="Zed", composio_managed_auth_schemes=["OAUTH2"]),
                _toolkit("mid", name="Mid", auth_schemes=["OAUTH2"]),
                _toolkit("abe", name="Abe", auth_schemes=["API_KEY"]),
            ]
        )
        assert [r.name for r in rows] == ["Abe", "Mid", "Zed"]

    def test_groups_are_ordered_by_what_a_business_asks_for(self):
        """Not alphabetically, and not by how many connectors are in each."""
        names = [name for name, _ in GROUPS]
        assert names[0] == "Messaging"
        assert names.index("Email") < names.index("Marketing")

    def test_an_app_lands_in_one_group_even_when_it_claims_several(self):
        """WhatsApp is 'phone & sms' and 'communication'; both map to
        Messaging, and it must appear once."""
        rows = curate(
            [
                _toolkit(
                    "whatsapp",
                    name="WhatsApp",
                    categories=("phone & sms", "communication"),
                    composio_managed_auth_schemes=["OAUTH2"],
                )
            ]
        )
        assert len(rows) == 1
        assert rows[0].group == "Messaging"


class TestNothingUsefulIsDroppedSilently:
    """The allowlist this replaced had no entry for "commerce", so Starshipit,
    HitPay, Order Desk and Lightspeed vanished and nothing said so. Every
    category Composio adds after today would have gone the same way."""

    def test_a_commerce_app_reaches_the_screen(self):
        rows = curate([_toolkit("starshipit", categories=("commerce",))])
        assert rows and rows[0].group == "Shop & shipping"

    @pytest.mark.parametrize("label", ["ecommerce", "e-commerce", "commerce", "retail"])
    def test_every_spelling_of_commerce_lands_on_one_shelf(self, label):
        rows = curate([_toolkit("shop", categories=(label,))])
        assert rows[0].group == "Shop & shipping"

    def test_a_category_nobody_anticipated_becomes_Other_not_nothing(self):
        """A shelf we did not think of is worth less than the right shelf and
        far more than silence."""
        rows = curate([_toolkit("weird", categories=("underwater basket weaving",))])
        assert rows and rows[0].group == "Other"

    def test_a_toolkit_claiming_no_category_at_all_still_appears(self):
        rows = curate([_toolkit("bare", categories=())])
        assert rows and rows[0].group == "Other"

    def test_a_useful_app_filed_under_developer_tools_lands_where_expected(self):
        """Google Maps is categorised 'developer tools' by Composio and is
        perfectly useful to a business. The business shelves are tried before
        the long-tail ones, so it lands where a business would look."""
        rows = curate(
            [_toolkit("google_maps", categories=("developer tools", "productivity"))]
        )
        assert rows and rows[0].group == "Work management"

    def test_nothing_offerable_is_dropped_for_its_category(self):
        """There is no category that removes a connector any more. The only
        reason not to list one is that we cannot connect it at all."""
        for label in ("developer tools", "gaming", "ai models", "made up thing"):
            assert curate([_toolkit("x", categories=(label,))]), label

    def test_other_sorts_last(self):
        rows = curate(
            [
                _toolkit("misc", name="Misc", categories=("misc",)),
                _toolkit("mail", name="Mail", categories=("email",)),
            ]
        )
        assert [r.group for r in rows] == ["Email", "Other"]


class TestPopularityOrder:
    def test_the_names_people_ask_for_come_first(self):
        """Alphabetical alone puts "2chat" and "AimTell" above WhatsApp, which
        is a correct sort and a useless screen."""
        rows = curate(
            [
                _toolkit("aimtell", name="AimTell", categories=("team chat",)),
                _toolkit("2chat", name="2chat", categories=("team chat",)),
                _toolkit("whatsapp", name="WhatsApp", categories=("team chat",)),
                _toolkit("slack", name="Slack", categories=("team chat",)),
            ]
        )
        assert [r.slug for r in rows][:2] == ["whatsapp", "slack"]

    def test_everything_unranked_is_alphabetical(self):
        rows = curate(
            [
                _toolkit("zeta", name="Zeta", categories=("team chat",)),
                _toolkit("alpha", name="Alpha", categories=("team chat",)),
                _toolkit("mid", name="Mid", categories=("team chat",)),
            ]
        )
        assert [r.name for r in rows] == ["Alpha", "Mid", "Zeta"]

    def test_setup_never_decides_the_order(self):
        """Razorpay needs a pasted key and every Indian business uses it.
        Sorting one-click first would bury it under an app nobody has heard
        of; the setup kind is a badge on the row, not a ranking."""
        rows = curate(
            [
                _toolkit(
                    "obscure",
                    name="Obscure",
                    categories=("payment processing",),
                    composio_managed_auth_schemes=["OAUTH2"],
                ),
                _toolkit(
                    "razorpay",
                    name="Razorpay",
                    categories=("payment processing",),
                    auth_schemes=["API_KEY"],
                ),
            ]
        )
        assert rows[0].slug == "razorpay"


class TestSearch:
    def _rows(self):
        return curate(
            [
                _toolkit(
                    "whatsapp",
                    name="WhatsApp",
                    categories=("team chat",),
                    composio_managed_auth_schemes=["OAUTH2"],
                ),
                _toolkit("whatagraph", name="Whatagraph", categories=("analytics",)),
                _toolkit("2chat", name="2chat", categories=("team chat",)),
                _toolkit("klaviyo", name="Klaviyo", categories=("marketing",)),
            ]
        )

    def test_a_name_that_starts_with_the_query_wins(self):
        """Somebody typing "what" wants WhatsApp, not Whatagraph."""
        found = search(self._rows(), "what")
        assert found[0].slug == "whatsapp"

    def test_a_description_match_ranks_below_a_name_match(self):
        rows = [
            *curate([_toolkit("klaviyo", name="Klaviyo", categories=("marketing",))]),
            *curate([_toolkit("chatapp", name="Chatapp", categories=("team chat",))]),
        ]
        found = search(rows, "chat")
        assert found[0].slug == "chatapp"

    def test_the_slug_is_searchable_when_the_name_is_not(self):
        """Google Sheets is "googlesheets" in every URL an operator has seen."""
        rows = curate(
            [
                _toolkit(
                    "googlesheets", name="Google Sheets", categories=("spreadsheets",)
                )
            ]
        )
        assert search(rows, "googlesheets")

    def test_search_is_case_and_space_insensitive(self):
        assert search(self._rows(), "  WHATSAPP ")[0].slug == "whatsapp"

    def test_an_empty_query_returns_everything_untouched(self):
        rows = self._rows()
        assert search(rows, "") == rows
        assert search(rows, "   ") == rows

    def test_a_query_matching_nothing_returns_nothing(self):
        assert search(self._rows(), "zzzznope") == []
