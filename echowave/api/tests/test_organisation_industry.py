"""What kind of business this is, learned from what it hired.

The account declared nothing, so the same question got asked every visit --
somebody says "Sunrise Dental in Bandra", hires a front desk, comes back next
month, and the chat starts from zero. `suggest_roles` ranked on the words typed
that minute rather than on what the account demonstrably is.

Two rules hold this file together, and both are about not lying:

**Never claim to know more than the action proves.** A pack serving four
industries does not say which of the four, so nothing here picks one. Filing a
salon as a clinic and then reading it back as fact is worse than not knowing.

**Never infer silently.** A guess that is wrong and invisible is the failure
this repository keeps finding. The inference is returned as a sentence for the
chat to say, and one word overturns it.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.services.packs import industry
from api.services.packs.catalogue import all_packs

TEMPLATES = {pack.slug: pack.template_id for pack in all_packs()}
CLINIC = TEMPLATES["front_desk_clinic"]
ORDERS = TEMPLATES["order_confirmation"]


class TestWhatTheHiringProves:
    def test_a_clinic_front_desk_narrows_to_its_industries(self):
        found = industry.industries_for_templates([CLINIC])
        assert "Dental" in found and "Clinics" in found

    def test_it_does_not_pick_one_of_four(self):
        """The rule this module exists for. `front_desk_clinic` serves clinics,
        dental, diagnostics and salons; choosing one would file a salon as a
        clinic and read as fact forever after."""
        found = industry.industries_for_templates([CLINIC])
        assert len(found) > 1

    def test_two_packs_that_overlap_narrow_to_the_overlap(self):
        """Two packs sharing an industry is a far stronger signal than either
        alone, so the intersection wins when there is one.

        Constructed rather than taken from the catalogue: no two shipped packs
        currently share an industry, so relying on real data would skip this
        branch and leave the narrowing untested -- which is the half of the
        design that makes hiring a second role worth anything.
        """
        from types import SimpleNamespace

        fake = (
            SimpleNamespace(
                template_id="t-a", industries=["Clinics", "Dental", "Salons"], name="A"
            ),
            SimpleNamespace(
                template_id="t-b", industries=["Dental", "Diagnostics"], name="B"
            ),
        )
        with patch("api.services.packs.catalogue.all_packs", lambda: fake):
            assert industry.industries_for_templates(["t-a", "t-b"]) == ("Dental",)

    def test_two_packs_that_share_nothing_report_both(self):
        """A business running a clinic desk and an order-confirmation agent
        genuinely spans two. An empty intersection would throw away what we
        know rather than report it."""
        found = industry.industries_for_templates([CLINIC, ORDERS])
        assert "Dental" in found
        assert "E-commerce" in found

    def test_nothing_hired_proves_nothing(self):
        assert industry.industries_for_templates([]) == ()
        assert industry.industries_for_templates(["not-a-template", ""]) == ()


class TestResolve:
    @pytest.mark.asyncio
    async def test_no_organization_is_unknown_rather_than_an_error(self):
        known = await industry.resolve(None)
        assert known["source"] == industry.SOURCE_UNKNOWN

    @pytest.mark.asyncio
    async def test_what_the_operator_told_us_beats_what_we_inferred(self):
        """They corrected us, or told us before we could guess. Re-deriving
        every turn would quietly overwrite the correction."""
        from api.schemas.organization_preferences import OrganizationPreferences

        with patch(
            "api.services.organization_preferences.get_organization_preferences",
            AsyncMock(return_value=OrganizationPreferences(industry="Dental lab")),
        ):
            known = await industry.resolve(42)

        assert known["source"] == industry.SOURCE_SET
        assert known["industry"] == "Dental lab"

    @pytest.mark.asyncio
    async def test_an_inference_is_never_written_into_industry(self):
        """A single candidate is still a guess. Storing it would turn "probably
        a clinic" into "is a clinic" without anybody agreeing to it."""
        from api.schemas.organization_preferences import OrganizationPreferences

        with (
            patch(
                "api.services.organization_preferences.get_organization_preferences",
                AsyncMock(return_value=OrganizationPreferences()),
            ),
            patch.object(
                db_client, "hired_template_ids", AsyncMock(return_value=[CLINIC])
            ),
        ):
            known = await industry.resolve(42)

        assert known["source"] == industry.SOURCE_HIRED
        assert known["industry"] is None
        assert known["candidates"]

    @pytest.mark.asyncio
    async def test_a_failed_read_is_unknown_and_never_raises(self):
        """This decorates a chat reply and a ranking. Neither is worth failing
        a conversation over."""
        from api.schemas.organization_preferences import OrganizationPreferences

        with (
            patch(
                "api.services.organization_preferences.get_organization_preferences",
                AsyncMock(return_value=OrganizationPreferences()),
            ),
            patch.object(
                db_client,
                "hired_template_ids",
                AsyncMock(side_effect=RuntimeError("database on fire")),
            ),
        ):
            known = await industry.resolve(42)

        assert known["source"] == industry.SOURCE_UNKNOWN


class TestRankingReordersAndNeverRemoves:
    def test_a_matching_pack_floats(self):
        known = {"candidates": ("Dental",)}
        ranked = sorted(all_packs(), key=industry.rank_key(known))
        assert "Dental" in ranked[0].industries

    def test_everything_survives_the_sort(self):
        """A clinic asking about payments should see the payment role -- ranked
        under its clinic roles, but present. Filtering would answer "we do not
        do that", which is false."""
        known = {"candidates": ("Dental",)}
        ranked = sorted(all_packs(), key=industry.rank_key(known))
        assert {p.slug for p in ranked} == {p.slug for p in all_packs()}

    def test_knowing_nothing_changes_no_order(self):
        """A new account must see the search's own relevance order, untouched."""
        packs = list(all_packs())
        ranked = sorted(packs, key=industry.rank_key({"candidates": ()}))
        assert [p.slug for p in ranked] == [p.slug for p in packs]


class TestTheSentenceTheChatSays:
    def test_an_inference_is_offered_for_correction(self):
        said = industry.sentence(
            {"source": industry.SOURCE_HIRED, "candidates": ("Clinics", "Dental")}
        )
        assert said and "tell me if that's wrong" in said

    def test_what_they_told_us_is_stated_not_questioned(self):
        """ "You're a clinic, right?" to somebody who typed "clinic" a moment
        ago reads as not listening."""
        said = industry.sentence(
            {
                "source": industry.SOURCE_SET,
                "industry": "Dental clinic",
                "candidates": ("Dental clinic",),
            }
        )
        assert said and "wrong" not in said

    def test_knowing_nothing_says_nothing(self):
        """Silence beats "I don't know what your business is" on a first turn."""
        assert (
            industry.sentence({"source": industry.SOURCE_UNKNOWN, "candidates": ()})
            is None
        )


class TestTheChatCanBeCorrected:
    def test_the_tool_exists_and_is_offered_beside_the_roles(self):
        from api.services.agent_builder.tools import TOOL_NAMES, tool_schemas

        assert "set_business_type" in TOOL_NAMES
        names = [t["name"] for t in tool_schemas()]
        assert names.index("suggest_roles") < names.index("set_business_type")

    def test_the_tool_refuses_to_argue_with_the_owner(self):
        from api.services.agent_builder.tools import tool_schemas

        described = next(t for t in tool_schemas() if t["name"] == "set_business_type")[
            "description"
        ]
        assert "never argue" in described.lower()

    @pytest.mark.asyncio
    async def test_an_empty_correction_is_refused(self):
        from api.services.agent_builder.tools import _set_business_type

        assert "error" in await _set_business_type(
            organization_id=42, business_type="   "
        )

    @pytest.mark.asyncio
    async def test_it_writes_only_the_one_field(self):
        """Preferences arrive as a whole object, and a save carrying schema
        defaults switched unrelated settings off -- the bug
        `with_staff_fields` exists for. Naming one field is what stops it."""
        from api.schemas.organization_preferences import OrganizationPreferences
        from api.services.agent_builder.tools import _set_business_type

        stored = OrganizationPreferences(
            timezone="Asia/Kolkata", byok_fallback_to_managed=True
        )
        with (
            patch(
                "api.services.organization_preferences.get_organization_preferences",
                AsyncMock(return_value=stored),
            ),
            patch(
                "api.services.organization_preferences.upsert_organization_preferences",
                AsyncMock(side_effect=lambda _org, prefs: prefs),
            ) as upsert,
        ):
            result = await _set_business_type(
                organization_id=42, business_type="  Dental laboratory  "
            )

        assert result["saved"] is True
        saved = upsert.await_args.args[1]
        assert saved.industry == "Dental laboratory"
        assert saved.timezone == "Asia/Kolkata"
        assert saved.byok_fallback_to_managed is True

    @pytest.mark.asyncio
    async def test_their_own_words_are_kept_not_mapped_to_a_category(self):
        """Our pack industries are shelving, not a list a business has to see
        itself in. Refusing "dental laboratory" teaches the operator we do not
        serve them."""
        from api.schemas.organization_preferences import OrganizationPreferences
        from api.services.agent_builder.tools import _set_business_type

        with (
            patch(
                "api.services.organization_preferences.get_organization_preferences",
                AsyncMock(return_value=OrganizationPreferences()),
            ),
            patch(
                "api.services.organization_preferences.upsert_organization_preferences",
                AsyncMock(side_effect=lambda _org, prefs: prefs),
            ),
        ):
            result = await _set_business_type(
                organization_id=42, business_type="D2C skincare brand"
            )

        assert result["business_type"] == "D2C skincare brand"


class TestSuggestRolesSaysWhatItAssumed:
    @pytest.mark.asyncio
    async def test_the_assumption_travels_with_the_roles(self):
        from api.services.agent_builder import tools as builder

        with patch.object(
            builder,
            "pack_industry",
            **{
                "resolve": AsyncMock(
                    return_value={
                        "source": industry.SOURCE_HIRED,
                        "candidates": ("Dental",),
                        "industry": None,
                    }
                ),
                "rank_key": industry.rank_key,
                "sentence": industry.sentence,
            },
        ):
            with patch.object(
                builder, "resolve_listed_packs", AsyncMock(return_value=all_packs())
            ):
                result = await builder._suggest_roles("clinic", organization_id=42)

        assert result["assumed_about_this_business"]
        assert "tell me if that's wrong" in result["assumed_about_this_business"]

    @pytest.mark.asyncio
    async def test_a_new_account_is_told_nothing_to_say(self):
        from api.services.agent_builder import tools as builder

        with (
            patch.object(
                builder, "resolve_listed_packs", AsyncMock(return_value=all_packs())
            ),
            patch.object(
                builder.pack_industry,
                "resolve",
                AsyncMock(
                    return_value={
                        "source": industry.SOURCE_UNKNOWN,
                        "candidates": (),
                        "industry": None,
                    }
                ),
            ),
        ):
            result = await builder._suggest_roles("clinic", organization_id=42)

        assert result["assumed_about_this_business"] is None
