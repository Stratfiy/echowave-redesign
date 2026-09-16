"""What the Integrations screen features, and why.

`POPULAR` is editorial: Composio publishes no popularity figure, and the
twelve at the front are the claim Decibyl makes about what it is for.

The order it had was not that claim. Four Google office apps took a third
of the shelf, Stripe outranked Razorpay in a product sold in India, two
Zoho finance apps sat in the top twelve, Slack was featured to a market
that lives on WhatsApp -- and Calendly, which does the single biggest job
a clinic front desk has, was not featured at all.

Every slug below was checked against the live catalogue: all 43 exist, so
none of this is a rename that silently stopped rendering.
"""

from api.routes.connectors import POPULAR_SHOWN
from api.services.integrations.composio.catalogue import POPULAR

FEATURED = POPULAR[:POPULAR_SHOWN]


class TestNothingWasLost:
    def test_it_is_a_reranking_not_a_cull(self):
        """The apps further down are ranked last, never deleted.

        Removing them would say "no opinion", and there is one: they are
        further from what a receptionist reaches for, not absent. A business
        that wants Jira still gets Jira.
        """
        assert len(POPULAR) == 43
        assert len(set(POPULAR)) == len(POPULAR), "a slug is listed twice"

    def test_the_demoted_ones_are_still_on_the_list(self):
        for slug in ("stripe", "salesforce", "slack", "jira", "zoho_invoice"):
            assert slug in POPULAR, slug


class TestTheShelfMatchesTheMarket:
    def test_booking_is_near_the_front(self):
        # The single biggest job a clinic front desk has.
        assert "googlecalendar" in FEATURED
        assert "calendly" in FEATURED, "the founder's own account connects it"

    def test_the_payment_app_this_market_uses_outranks_the_one_it_does_not(self):
        assert "razorpay" in FEATURED
        assert POPULAR.index("razorpay") < POPULAR.index("stripe")

    def test_the_channel_this_market_lives_on_is_first(self):
        assert POPULAR[0] == "whatsapp"

    def test_slack_is_not_featured_to_a_whatsapp_market(self):
        assert "slack" not in FEATURED

    def test_one_finance_app_per_vendor_on_the_shelf(self):
        # Zoho Books and Zoho Invoice both sat in the top twelve.
        assert "zoho_invoice" not in FEATURED

    def test_the_office_suite_does_not_take_a_third_of_the_shelf(self):
        # Sheets and Calendar earn their place; Docs and Drive were there
        # because they came in the same box.
        google = [s for s in FEATURED if s.startswith("google")]
        assert len(google) <= 2, google

    def test_no_project_tracker_is_featured(self):
        # The customer is a clinic, not a software team.
        for slug in ("trello", "asana", "clickup", "linear", "jira"):
            assert slug not in FEATURED, slug

    def test_no_social_publishing_is_featured(self):
        # Not what a voice bot does.
        for slug in ("instagram", "facebook", "linkedin", "youtube"):
            assert slug not in FEATURED, slug


class TestTheRankStillWorks:
    def test_every_featured_slug_has_a_rank(self):
        from api.services.integrations.composio.catalogue import _POPULAR_RANK

        for slug in FEATURED:
            assert slug in _POPULAR_RANK, slug

    def test_the_rank_follows_the_order(self):
        from api.services.integrations.composio.catalogue import _POPULAR_RANK

        assert _POPULAR_RANK["whatsapp"] < _POPULAR_RANK["googlecalendar"]
        assert _POPULAR_RANK["freshdesk"] < _POPULAR_RANK["zendesk"]
