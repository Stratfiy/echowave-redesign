"""A vendor we run ourselves must not be offered as a paste-a-key connector.

Plivo is the case that matters and the one that was live. Searching "pli" on
the Apps screen returned an ordinary card reading "Needs your key -- paste a
key from that app", for the carrier every Decibyl call runs on. Following it
stores a second copy of the customer's Plivo credentials somewhere no call
path reads, shows Connected, and lets them believe their number is live.

The tests below are about what the screen *says*, which is the part that was
wrong. They do not assert that the connector is hidden -- Composio's Plivo
toolkit is a real agent capability and hiding it would take a working feature
away to fix a wording bug.
"""

from api.services.integrations.composio.catalogue import (
    OURS,
    SETUP_API_KEY,
    SETUP_NEEDS_APPROVAL,
    SETUP_ONE_CLICK,
    SETUP_OURS,
    curate,
)


def _toolkit(slug: str, name: str, **kwargs):
    toolkit = {
        "slug": slug,
        "name": name,
        "meta": {
            "categories": [{"name": "phone & sms"}],
            "description": f"{name} does things.",
            "tools_count": 4,
        },
    }
    toolkit.update(kwargs)
    return toolkit


def _by_slug(rows, slug):
    return next((row for row in rows if row.slug == slug), None)


class TestPlivo:
    def test_plivo_is_marked_as_ours_not_as_a_key_to_paste(self):
        # Exactly the shape Composio publishes it as: an API-key toolkit.
        rows = curate([_toolkit("plivo", "Plivo", auth_schemes=["API_KEY"])])
        plivo = _by_slug(rows, "plivo")
        assert plivo is not None, "Plivo must still appear in the catalogue"
        assert plivo.setup == SETUP_OURS
        assert plivo.setup != SETUP_API_KEY

    def test_plivo_points_at_the_telephony_screen(self):
        rows = curate([_toolkit("plivo", "Plivo", auth_schemes=["API_KEY"])])
        assert _by_slug(rows, "plivo").setup_url == "/telephony-configurations"

    def test_a_vendor_composio_cannot_offer_at_all_still_gets_a_row(self):
        # No auth schemes: _setup_kind returns None and the row would be
        # dropped. We can connect it ourselves, and the row is the only thing
        # on the screen that says so.
        rows = curate([_toolkit("plivo", "Plivo")])
        plivo = _by_slug(rows, "plivo")
        assert plivo is not None
        assert plivo.setup == SETUP_OURS
        assert plivo.also_connectable is False


class TestSayingBoth:
    """Ours is not a reason to remove a capability that works."""

    def test_a_one_click_native_vendor_is_still_connectable_as_a_tool(self):
        rows = curate(
            [
                _toolkit(
                    "plivo",
                    "Plivo",
                    composio_managed_auth_schemes=["OAUTH2"],
                )
            ]
        )
        plivo = _by_slug(rows, "plivo")
        assert plivo.setup == SETUP_OURS
        assert plivo.also_connectable is True

    def test_a_key_only_native_vendor_offers_no_connect_button(self):
        # The UI has no screen for pasting a key, so offering the button would
        # be a button that cannot work.
        rows = curate([_toolkit("twilio", "Twilio", auth_schemes=["API_KEY"])])
        assert _by_slug(rows, "twilio").also_connectable is False


class TestEveryoneElseIsUnchanged:
    def test_an_ordinary_one_click_app_has_no_native_screen(self):
        rows = curate(
            [_toolkit("slack", "Slack", composio_managed_auth_schemes=["OAUTH2"])]
        )
        slack = _by_slug(rows, "slack")
        assert slack.setup == SETUP_ONE_CLICK
        assert slack.setup_url is None
        assert slack.also_connectable is False

    def test_an_ordinary_key_app_still_says_needs_your_key(self):
        rows = curate([_toolkit("razorpay", "Razorpay", auth_schemes=["API_KEY"])])
        assert _by_slug(rows, "razorpay").setup == SETUP_API_KEY

    def test_an_unregistered_oauth_app_still_says_ask_us(self):
        rows = curate([_toolkit("docusign", "DocuSign", auth_schemes=["OAUTH2"])])
        assert _by_slug(rows, "docusign").setup == SETUP_NEEDS_APPROVAL


class TestMatchingIsHardToGetWrong:
    """The rule must not fail the way this codebase keeps failing.

    An entry keyed on a slug we guessed wrong matches nothing, the generic
    card renders, and nobody ever finds out -- which is the whole silent
    absence class. So the name is matched too.
    """

    def test_a_renamed_slug_still_matches_on_the_display_name(self):
        rows = curate(
            [_toolkit("plivo_communications", "Plivo", auth_schemes=["API_KEY"])]
        )
        assert _by_slug(rows, "plivo_communications").setup == SETUP_OURS

    def test_a_spaced_or_hyphenated_name_matches(self):
        rows = curate([_toolkit("gcal_x", "Google Calendar", auth_schemes=["API_KEY"])])
        assert _by_slug(rows, "gcal_x").setup_url == "/integrations/apps"

    def test_every_telephony_provider_we_support_has_an_entry(self):
        # A provider in services/telephony/providers/ with no entry here is a
        # card telling somebody to paste a carrier key we will never read.
        for provider in ("plivo", "twilio", "telnyx", "vonage"):
            assert provider in OURS, f"{provider} has no native screen mapped"

    def test_no_entry_points_at_an_off_site_url(self):
        # These render as an in-app <Link>. An absolute URL would break it.
        for slug, url in OURS.items():
            assert url.startswith("/"), f"{slug} maps to {url!r}"
