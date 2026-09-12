"""The shelf, its search, and the chat that offers a role before building one.

The failure this file guards against is not an exception. It is a role that
exists and cannot be found -- which looks to a customer, and to the builder
chat, exactly like a role we do not have.
"""

from types import SimpleNamespace

import pytest

from api.routes.packs import pack_detail, shelf
from api.services.agent_builder.tools import _suggest_roles, tool_schemas
from api.services.packs import catalogue
from api.services.packs.search import filter_packs, search_packs

SHELF = catalogue._packs("+911234567890")


def _user():
    return SimpleNamespace(selected_organization_id=42, id=1)


class TestSearchNeverLosesARole:
    def test_a_short_or_empty_query_browses_rather_than_finding_nothing(self):
        """Two characters typed means "show me what you have", and an empty
        result would read as "we have nothing for you"."""
        assert len(search_packs("", packs=SHELF)) == len(SHELF)
        assert len(search_packs("a", packs=SHELF)) == len(SHELF)

    def test_the_words_a_real_person_uses_reach_the_right_role(self):
        found = search_packs("my clinic phone rings all day", packs=SHELF)
        assert found[0].slug == "front_desk_clinic"

    def test_the_roles_own_name_outranks_prose_that_merely_mentions_it(self):
        found = search_packs("front desk", packs=SHELF)
        assert found[0].name == "Front Desk"

    def test_an_ecommerce_sentence_reaches_order_confirmation(self):
        found = search_packs("we ship cod orders and half of them bounce", packs=SHELF)
        assert "order_confirmation" in {pack.slug for pack in found}

    def test_nothing_matching_is_dropped(self):
        """A role ranked third is fine. A role that cannot be found is not."""
        for pack in SHELF:
            found = search_packs(pack.name, packs=SHELF)
            assert pack.slug in {match.slug for match in found}, pack.slug


class TestFilters:
    def test_industry_matches_membership_not_equality(self):
        """A role claiming four industries has to be findable by all four."""
        for industry in ("Clinics", "Dental", "Salons"):
            found = filter_packs(industry=industry, packs=SHELF)
            assert "front_desk_clinic" in {pack.slug for pack in found}, industry

    def test_language_matches_membership_too(self):
        found = filter_packs(language="ta", packs=SHELF)
        assert found

    def test_calling_splits_the_shelf_by_what_it_costs(self):
        """The split that decides the bill: a calling role is a seat, anything
        else runs on the monthly plan. Nothing may land in both."""
        calling = {pack.slug for pack in filter_packs(calling=True, packs=SHELF)}
        text_only = {pack.slug for pack in filter_packs(calling=False, packs=SHELF)}
        assert calling
        assert not calling & text_only
        assert calling | text_only == {pack.slug for pack in SHELF}

    def test_a_filter_that_matches_nothing_returns_nothing_rather_than_everything(self):
        assert filter_packs(industry="Shipbuilding", packs=SHELF) == ()


class TestTheShelfEndpoint:
    @pytest.mark.asyncio
    async def test_search_and_filters_narrow_together(self, monkeypatch):
        monkeypatch.setattr("api.routes.packs.search_packs", lambda q: SHELF)
        response = await shelf(
            q="phone",
            job=None,
            industry="Clinics",
            language=None,
            calling=None,
            user=_user(),
        )
        assert [pack.slug for pack in response.packs] == ["front_desk_clinic"]

    @pytest.mark.asyncio
    async def test_a_card_carries_the_computed_price_not_a_typed_one(self, monkeypatch):
        monkeypatch.setattr("api.routes.packs.search_packs", lambda q: SHELF)
        response = await shelf(
            q=None,
            job=None,
            industry=None,
            language=None,
            calling=True,
            user=_user(),
        )
        assert response.packs
        for pack in response.packs:
            assert pack.pricing.is_hire is True
            assert pack.pricing.included_minutes > 0
            assert pack.badges

    @pytest.mark.asyncio
    async def test_an_unlisted_role_does_not_exist_from_outside(self, monkeypatch):
        """Not a 403. A role awaiting review is not a permission problem, and
        saying "you may not see this" leaks that it exists."""
        from fastapi import HTTPException

        monkeypatch.setattr("api.routes.packs.get_pack", lambda slug: None)
        with pytest.raises(HTTPException) as raised:
            await pack_detail(slug="front_desk_clinic", user=_user())
        assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_carries_the_steps_and_the_compliance_notes(self, monkeypatch):
        monkeypatch.setattr(
            "api.routes.packs.get_pack",
            lambda slug: next(p for p in SHELF if p.slug == slug),
        )
        detail = await pack_detail(slug="front_desk_clinic", user=_user())
        assert [step.key for step in detail.steps][0] == "hear_it"
        assert detail.compliance_notes


class TestTheChatOffersHiringFirst:
    def test_the_tool_exists_and_is_offered_before_the_template_list(self):
        """Order in the catalogue is a nudge the model reads. Hiring something
        proven beats generating a first draft onto a live phone line."""
        names = [tool["name"] for tool in tool_schemas()]
        assert names[0] == "suggest_roles"
        assert names.index("suggest_roles") < names.index("list_agent_templates")

    def test_it_answers_with_roles_the_model_can_read_out(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.agent_builder.tools.search_packs", lambda q: SHELF
        )
        result = _suggest_roles("dental clinic in hosur")
        assert result["roles"]
        first = result["roles"][0]
        assert first["does"]
        assert first["monthly_price_rupees"] == 6999
        assert first["priced_as"] == "a hire, per agent"
        assert first["needs_connected"]

    def test_at_most_four_roles_so_the_reply_stays_readable(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.agent_builder.tools.search_packs", lambda q: SHELF
        )
        assert len(_suggest_roles("")["roles"]) <= 4

    def test_an_empty_shelf_is_reported_as_empty_not_as_nothing_to_offer(
        self, monkeypatch
    ):
        """With no demo number configured every calling role is unlisted. A
        model handed a bare `[]` concludes we have nothing and starts building,
        which is the wrong answer to a configuration problem."""
        monkeypatch.setattr(
            "api.services.agent_builder.tools.search_packs", lambda q: ()
        )
        result = _suggest_roles("clinic")
        assert result["roles"] == []
        assert "note" in result and result["note"]
