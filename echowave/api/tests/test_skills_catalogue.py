"""The shipped skills parse, and the shelf can describe them."""

from __future__ import annotations

from api.services.skills import catalogue
from api.services.skills.document import prompt_block


class TestWhatShips:
    def test_every_file_parses_and_carries_its_credit(self):
        skills = catalogue.all_skills()
        assert len(skills) >= 50, "the catalogue should not be nearly empty"
        for slug, skill in skills.items():
            assert slug == slug.lower()
            assert skill.title and skill.description
            assert skill.division
            # Redistributed from somebody else's repository: the source and
            # the licence are the terms, not decoration.
            assert skill.source, slug
            assert skill.license, slug
            assert skill.skill.line_count <= 500, slug

    def test_divisions_lead_with_the_big_shelves(self):
        divisions = catalogue.divisions()
        assert divisions, "no divisions"
        counts = [
            sum(1 for s in catalogue.all_skills().values() if s.division == d)
            for d in divisions
        ]
        assert counts == sorted(counts, reverse=True)

    def test_the_credit_line_names_both_repositories(self):
        sources = {row["source"] for row in catalogue.attributions()}
        assert "msitarzewski/agency-agents" in sources
        assert all(row["license"] for row in catalogue.attributions())

    def test_a_card_is_a_card_and_not_the_whole_body(self):
        skill = next(iter(catalogue.all_skills().values()))
        card = skill.as_card()
        assert set(card) == {
            "slug",
            "title",
            "description",
            "division",
            "emoji",
            "source",
            "license",
            "lines",
        }
        assert "body" not in card

    def test_a_skill_becomes_a_delimited_prompt_block(self):
        skill = catalogue.get("sales-coach")
        assert skill is not None
        block = prompt_block(skill.skill)
        assert block.startswith('<skill name="sales-coach">')
        assert block.rstrip().endswith("</skill>")

    def test_an_unknown_slug_is_none_rather_than_a_raise(self):
        assert catalogue.get("no-such-skill") is None
        assert catalogue.get("") is None
