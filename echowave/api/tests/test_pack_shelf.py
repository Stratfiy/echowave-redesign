"""The shelf, its search, and the chat that offers a role before building one.

The failure this file guards against is not an exception. It is a role that
exists and cannot be found -- which looks to a customer, and to the builder
chat, exactly like a role we do not have.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.packs import pack_detail, shelf
from api.services.agent_builder.tools import _suggest_roles, tool_schemas
from api.services.packs import catalogue, hire_steps
from api.services.packs._base import Channel
from api.services.packs.search import filter_packs, search_packs

SHELF = catalogue._packs("+911234567890")

#: The channels that put a role on a phone, so the demo assertions below can
#: say "every calling role" and mean it. The shelf now holds roles that answer
#: nothing, and a demo number on one of those would be a link to nowhere.
CALLING = {Channel.INBOUND_CALL, Channel.OUTBOUND_CALL}


def _calling(packs) -> list:
    return [pack for pack in packs if set(pack.channels) & CALLING]


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
        # Pinned to the slug, not the display name: a rename is a product
        # decision and must not be able to break a ranking test.
        assert found[0].slug == "front_desk_clinic"

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
        """A filter must narrow, and an unmatched one must not fall open.

        Asserted on `job` rather than on `industry`. An industry nobody serves
        no longer empties the shelf, and correctly so: a role declaring no
        industries serves every industry, so an internal knowledge bot really
        is available to a shipbuilder. The property this test exists for --
        that a filter narrows rather than falling open -- is unchanged, and
        `job` is where it can still be shown.
        """
        assert filter_packs(job="Fly the aeroplane", packs=SHELF) == ()

    def test_an_unserved_industry_returns_only_the_roles_that_fit_anywhere(self):
        """The other half of that change, made explicit.

        Not "returns everything": the vertical roles are still excluded, which
        is what proves the industry filter is doing its job.
        """
        found = filter_packs(industry="Shipbuilding", packs=SHELF)
        assert found, "a horizontal role should reach any industry"
        assert all(not pack.industries for pack in found)
        assert "front_desk_clinic" not in {pack.slug for pack in found}


class TestTheShelfEndpoint:
    @pytest.mark.asyncio
    async def test_search_and_filters_narrow_together(self, monkeypatch):
        monkeypatch.setattr(
            "api.routes.packs.resolve_listed_packs", AsyncMock(return_value=SHELF)
        )
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
    async def test_a_card_carries_how_it_charges_not_a_typed_price(self, monkeypatch):
        monkeypatch.setattr(
            "api.routes.packs.resolve_listed_packs", AsyncMock(return_value=SHELF)
        )
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
            # No monthly figure on the card any more: the plan is a fixed
            # platform charge and running the role draws on credit. What the
            # card must carry is whether the plan needs voice and what unit
            # the work is metered in.
            assert pack.charging.needs_voice is True
            assert pack.charging.unit == "minute"
            assert pack.charging.hire_price_paise == 0
            assert pack.badges

    @pytest.mark.asyncio
    async def test_an_unlisted_role_does_not_exist_from_outside(self, monkeypatch):
        """Not a 403. A role awaiting review is not a permission problem, and
        saying "you may not see this" leaks that it exists."""
        from fastapi import HTTPException

        monkeypatch.setattr(
            "api.routes.packs.resolve_pack", AsyncMock(return_value=None)
        )
        with pytest.raises(HTTPException) as raised:
            await pack_detail(slug="front_desk_clinic", user=_user())
        assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_carries_the_steps_and_the_compliance_notes(self, monkeypatch):
        monkeypatch.setattr(
            "api.routes.packs.resolve_pack",
            AsyncMock(
                return_value=next(p for p in SHELF if p.slug == "front_desk_clinic")
            ),
        )
        detail = await pack_detail(slug="front_desk_clinic", user=_user())
        assert [step.key for step in detail.steps][0] == "interview"
        assert detail.flow == "voice"
        assert detail.compliance_notes


class TestTheChatOffersHiringFirst:
    def test_the_tool_exists_and_is_offered_before_the_template_list(self):
        """Order in the catalogue is a nudge the model reads. Hiring something
        proven beats generating a first draft onto a live phone line."""
        names = [tool["name"] for tool in tool_schemas()]
        assert names[0] == "suggest_roles"
        assert names.index("suggest_roles") < names.index("list_agent_templates")

    @pytest.mark.asyncio
    async def test_it_answers_with_roles_the_model_can_read_out(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.agent_builder.tools.resolve_listed_packs",
            AsyncMock(return_value=SHELF),
        )
        result = await _suggest_roles("dental clinic in hosur")
        assert result["roles"]
        first = result["roles"][0]
        assert first["does"]
        # No monthly figure. Hiring is included in the plan, so what the
        # model is given is what RUNNING it draws on -- and it used to be
        # handed "6999", a number nothing in billing ever charged.
        assert "Included in your plan" in first["costs"]
        assert "by the minute" in first["costs"]
        assert first["needs_voice_on_their_plan"] is True
        assert "monthly_price_rupees" not in first
        assert first["needs_connected"]

    @pytest.mark.asyncio
    async def test_at_most_four_roles_so_the_reply_stays_readable(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.agent_builder.tools.resolve_listed_packs",
            AsyncMock(return_value=SHELF),
        )
        assert len((await _suggest_roles(""))["roles"]) <= 4

    @pytest.mark.asyncio
    async def test_an_empty_shelf_is_reported_as_empty_not_as_nothing_to_offer(
        self, monkeypatch
    ):
        """With no demo line marked every calling role is unlisted. A model
        handed a bare `[]` concludes we have nothing and starts building, which
        is the wrong answer to a configuration problem."""
        monkeypatch.setattr(
            "api.services.agent_builder.tools.resolve_listed_packs",
            AsyncMock(return_value=()),
        )
        result = await _suggest_roles("clinic")
        assert result["roles"] == []
        assert "note" in result and result["note"]


class TestHowAProspectHearsIt:
    """Marking one agent as the demo is what takes the shelf from empty to
    listed, and it must take effect without a redeploy.

    A share link or a number will do. Requiring the number specifically would
    gate the whole shelf on a telephony purchase, which is the wrong thing for
    a listing to depend on.
    """

    def _contact(self, url=None, number=None, name="Meera"):
        return {"workflow_id": "1", "name": name, "url": url, "number": number}

    @pytest.mark.asyncio
    async def test_a_share_link_alone_lists_every_calling_role(self):
        """The link costs nothing per demo, works from the card, reaches a
        prospect abroad, and its text chat still works on a network where
        WebRTC will not connect."""
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(return_value=self._contact(url="https://app/talk/abc")),
        ):
            shelf_packs = await catalogue.resolve_listed_packs()
        # Calling roles only, as the name says. A role that answers no phone
        # is listed without a demo because it has nothing to demonstrate.
        calling = [p for p in shelf_packs if set(p.channels) & CALLING]
        assert calling
        assert all(pack.demo_url == "https://app/talk/abc" for pack in calling)
        assert all(pack.demo_number is None for pack in calling)

    @pytest.mark.asyncio
    async def test_a_number_alone_also_lists_them(self):
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(return_value=self._contact(number="+911234567890")),
        ):
            shelf_packs = await catalogue.resolve_listed_packs()
        calling = [p for p in shelf_packs if set(p.channels) & CALLING]
        assert calling
        assert all(pack.demo_number == "+911234567890" for pack in calling)

    @pytest.mark.asyncio
    async def test_both_are_offered_when_both_exist(self):
        """The number is stronger proof for a product whose pitch is that it
        answers your phone, so a card should be able to offer both."""
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(
                return_value=self._contact(
                    url="https://app/talk/abc", number="+911234567890"
                )
            ),
        ):
            pack = (await catalogue.resolve_listed_packs())[0]
        assert pack.demo_url and pack.demo_number
        step = next(s for s in hire_steps(pack) if s["key"] == "interview")
        assert step["demo_url"] and step["demo_number"]

    @pytest.mark.asyncio
    async def test_the_interview_step_never_tells_them_to_ring_a_number_we_lack(self):
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(return_value=self._contact(url="https://app/talk/abc")),
        ):
            pack = (await catalogue.resolve_listed_packs())[0]
        step = next(s for s in hire_steps(pack) if s["key"] == "interview")
        assert "Ring" not in step["detail"]

    @pytest.mark.asyncio
    async def test_no_demo_agent_unlists_every_calling_role(self):
        """A shelf full of dead demo links is one nobody reports.

        No longer "leaves the shelf empty". The roles that answer no phone
        have nothing to demonstrate and stay listed, so an account with no
        telephony still sees a marketplace with something in it -- which is
        better than the empty one this test used to require.
        """
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(return_value=self._contact()),
        ):
            shelf_packs = await catalogue.resolve_listed_packs()
        assert _calling(shelf_packs) == []
        assert shelf_packs, "the non-calling roles should still be on the shelf"

    @pytest.mark.asyncio
    async def test_a_database_that_will_not_answer_falls_back_rather_than_failing(
        self,
    ):
        """Worst case the calling roles stay unlisted, which is the same state
        as having marked no demo agent. A shelf that 500s teaches nobody
        anything."""
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(side_effect=RuntimeError("database on fire")),
        ):
            shelf_packs = await catalogue.resolve_listed_packs()
        assert _calling(shelf_packs) == []

    @pytest.mark.asyncio
    async def test_it_is_not_cached_so_marking_one_takes_effect_immediately(self):
        """Caching it would mean somebody marking a demo agent and then
        wondering why the shelf is still empty -- the exact confusion an
        environment variable caused."""
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(return_value=self._contact()),
        ):
            assert _calling(await catalogue.resolve_listed_packs()) == []
        with patch(
            "api.services.packs.catalogue.db_client.demo_contact",
            AsyncMock(return_value=self._contact(url="https://app/talk/xyz")),
        ):
            assert len(await catalogue.resolve_listed_packs()) == len(SHELF)
