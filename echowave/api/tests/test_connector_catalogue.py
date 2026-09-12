"""Curation: which of Composio's 1,540 toolkits an operator is shown, and how.

The tests here are about the two claims the screen makes. That a row labelled
one-click really can be connected in one click, and that a row we cannot offer
at all is still honest about it rather than absent.
"""

from api.services.integrations.composio.catalogue import (
    GROUPS,
    SETUP_API_KEY,
    SETUP_NEEDS_APPROVAL,
    SETUP_NO_AUTH,
    SETUP_ONE_CLICK,
    curate,
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


class TestWhatIsHidden:
    def test_developer_tooling_never_reaches_a_clinic_owner(self):
        """Composio's largest category is developer tools. A dental clinic is
        not looking for Hugging Face, and the groups are an allowlist so it
        simply never appears."""
        rows = curate([_toolkit("hugging_face", categories=("developer tools",))])
        assert rows == []

    def test_the_mcp_flavour_of_an_app_is_not_a_second_row(self):
        """Composio ships `<app>_mcp` beside many toolkits. Two Box rows
        differing only by a suffix is a worse screen, not a richer one."""
        rows = curate(
            [
                _toolkit("box", name="Box", categories=("documents",)),
                _toolkit("box_mcp", name="Box MCP", categories=("documents",)),
            ]
        )
        assert [r.slug for r in rows] == ["box"]

    def test_a_deprecated_toolkit_is_dropped(self):
        assert curate([_toolkit("old_app", deprecated=True)]) == []

    def test_a_row_with_no_slug_or_name_cannot_crash_the_screen(self):
        assert curate([{"slug": "x"}, {"name": "y"}, {}, None, "nonsense"]) == []


class TestOrdering:
    def test_one_click_apps_come_before_ones_needing_work(self):
        """The screen is read top down. What can be turned on now belongs
        above what cannot."""
        rows = curate(
            [
                _toolkit("needs", name="Needs", auth_schemes=["OAUTH2"]),
                _toolkit("keyed", name="Keyed", auth_schemes=["API_KEY"]),
                _toolkit("easy", name="Easy", composio_managed_auth_schemes=["OAUTH2"]),
            ]
        )
        assert [r.slug for r in rows] == ["easy", "keyed", "needs"]

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
