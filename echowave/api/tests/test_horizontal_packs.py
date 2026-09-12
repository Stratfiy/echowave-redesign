"""A role that fits every business must not vanish from every shelf.

Some jobs genuinely repeat across verticals -- a support bot answering from
the knowledge base, a compliance bot reminding staff about a schedule. The
honest declaration for one of those is an empty ``industries`` list rather
than a twenty-entry list that has to be edited every time we add a vertical.

Read the wrong way round, empty meant "matches nothing", and a horizontal role
would disappear from every industry filter in the marketplace with nothing
saying so.
"""

from api.services.packs._base import (
    AgentPack,
    Channel,
    Publisher,
)
from api.services.packs.search import filter_packs, search_packs

DECIBYL = Publisher(slug="decibyl", name="Decibyl", first_party=True)


def pack(slug, *, industries, job="Answer the phone"):
    return AgentPack(
        slug=slug,
        name=slug.replace("_", " ").title(),
        summary="Does a job.",
        job=job,
        publisher=DECIBYL,
        channels=[Channel.INBOUND_CALL],
        template_id="clinic_appointment",
        industries=industries,
        demo_url="https://example.test/demo",
    )


CLINIC = pack("front_desk", industries=["Clinics", "Dental"])
#: The case this file exists for.
SUPPORT = pack("support_knowledge", industries=[], job="Answer questions")


class TestHorizontalRoles:
    def test_a_role_with_no_industries_appears_on_every_shelf(self):
        for industry in ("Clinics", "E-commerce", "Logistics", "anything at all"):
            found = filter_packs(industry=industry, packs=[CLINIC, SUPPORT])
            assert SUPPORT in found, industry

    def test_it_appears_alongside_the_vertical_role_not_instead_of_it(self):
        found = filter_packs(industry="Clinics", packs=[CLINIC, SUPPORT])
        assert [p.slug for p in found] == ["front_desk", "support_knowledge"]

    def test_it_still_appears_with_no_filter_at_all(self):
        found = filter_packs(packs=[CLINIC, SUPPORT])
        assert SUPPORT in found


class TestVerticalRolesStayVertical:
    """The fix must not turn every filter into a no-op."""

    def test_a_clinic_role_is_not_offered_to_a_logistics_company(self):
        found = filter_packs(industry="Logistics", packs=[CLINIC, SUPPORT])
        assert CLINIC not in found
        assert SUPPORT in found

    def test_the_match_is_still_case_insensitive(self):
        assert CLINIC in filter_packs(industry="clinics", packs=[CLINIC])

    def test_a_role_is_findable_by_any_of_its_industries(self):
        assert CLINIC in filter_packs(industry="Dental", packs=[CLINIC])

    def test_other_filters_are_unaffected(self):
        # The job filter is equality and must stay that way -- a horizontal
        # role is horizontal about industry, not about what it does.
        found = filter_packs(job="Answer questions", packs=[CLINIC, SUPPORT])
        assert found == (SUPPORT,)


class TestSearchStillReadsIndustries:
    def test_a_vertical_role_is_searchable_by_its_industry_word(self):
        # search_packs scores over name, job, summary and industries. A
        # horizontal role has no industry words to score on, which is correct
        # -- nobody typing "dental" wants the generic support bot first.
        found = search_packs("dental", packs=[CLINIC, SUPPORT])
        assert found and found[0] is CLINIC
