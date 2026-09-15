"""Installing a skill, and putting it on a set of bots.

Against the real database: the whole point of the multi-select is that
unticking a bot removes the row, and a mocked session cannot show that.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationModel, UserModel, WorkflowModel
from api.services.skills import catalogue, shelf

SLUG = "sales-coach"
OTHER = "brand-voice"


async def _account(session, *, slug: str = "skills"):
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add_all([user, org])
    await session.flush()
    bots = []
    for name in ("Front desk", "Chaser", "Sales"):
        bot = WorkflowModel(
            name=name,
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        session.add(bot)
        bots.append(bot)
    await session.flush()
    return user, org, bots


@pytest.mark.asyncio
class TestKeepingASkill:
    async def test_installing_twice_is_installing_once(self, async_session, db_session):
        _, org, _ = await _account(async_session)
        await shelf.install(org.id, SLUG)
        await shelf.install(org.id, SLUG)
        have = await shelf.installed(org.id)
        assert list(have) == [SLUG]
        assert have[SLUG].on_bots == ()

    async def test_a_skill_that_does_not_ship_is_refused(
        self, async_session, db_session
    ):
        _, org, _ = await _account(async_session, slug="unknown")
        with pytest.raises(shelf.SkillError):
            await shelf.install(org.id, "no-such-skill")

    async def test_uninstalling_takes_it_off_every_bot(self, async_session, db_session):
        _, org, bots = await _account(async_session, slug="uninstall")
        await shelf.set_bots(org.id, SLUG, [bots[0].id, bots[1].id])
        await shelf.uninstall(org.id, SLUG)
        assert await shelf.installed(org.id) == {}
        assert await shelf.for_workflow(org.id, bots[0].id) == []


@pytest.mark.asyncio
class TestPuttingItOnBots:
    async def test_the_set_is_the_whole_answer_so_unticking_removes(
        self, async_session, db_session
    ):
        _, org, bots = await _account(async_session, slug="multi")
        await shelf.set_bots(org.id, SLUG, [bots[0].id, bots[1].id, bots[2].id])
        have = await shelf.installed(org.id)
        assert sorted(have[SLUG].on_bots) == sorted(b.id for b in bots)

        # Two ticked, one unticked: the unticked one loses it.
        await shelf.set_bots(org.id, SLUG, [bots[0].id])
        have = await shelf.installed(org.id)
        assert have[SLUG].on_bots == (bots[0].id,)
        assert await shelf.for_workflow(org.id, bots[1].id) == []
        # Still installed with nothing ticked at all.
        await shelf.set_bots(org.id, SLUG, [])
        have = await shelf.installed(org.id)
        assert SLUG in have and have[SLUG].on_bots == ()

    async def test_attaching_installs_it_so_the_shelf_can_show_it(
        self, async_session, db_session
    ):
        _, org, bots = await _account(async_session, slug="implicit")
        await shelf.set_bots(org.id, SLUG, [bots[0].id])
        assert SLUG in await shelf.installed(org.id)

    async def test_another_tenants_bot_is_refused_before_anything_is_written(
        self, async_session, db_session
    ):
        _, mine, my_bots = await _account(async_session, slug="mine")
        _, theirs, their_bots = await _account(async_session, slug="theirs")
        with pytest.raises(shelf.SkillError):
            await shelf.set_bots(mine.id, SLUG, [my_bots[0].id, their_bots[0].id])
        # Nothing written: not even the bot that was mine.
        assert await shelf.for_workflow(mine.id, my_bots[0].id) == []

    async def test_a_bot_carries_a_bounded_number(self, async_session, db_session):
        _, org, bots = await _account(async_session, slug="cap")
        slugs = list(catalogue.all_skills())[: shelf.MAX_PER_BOT + 1]
        for slug in slugs[: shelf.MAX_PER_BOT]:
            await shelf.set_bots(org.id, slug, [bots[0].id])
        with pytest.raises(shelf.SkillError):
            await shelf.set_bots(org.id, slugs[shelf.MAX_PER_BOT], [bots[0].id])


@pytest.mark.asyncio
class TestTheShelf:
    async def test_installed_comes_apart_from_the_rest_and_names_the_bots(
        self, async_session, db_session
    ):
        _, org, bots = await _account(async_session, slug="shelf")
        await shelf.set_bots(org.id, SLUG, [bots[0].id])
        await shelf.install(org.id, OTHER)
        view = await shelf.shelf(org.id)
        installed = {card["slug"]: card for card in view["installed"]}
        assert set(installed) == {SLUG, OTHER}
        assert installed[SLUG]["on_bots"] == [{"id": bots[0].id, "name": "Front desk"}]
        assert installed[OTHER]["on_bots"] == []
        # Everything else is on the shelf, and nothing is in both.
        rest = {card["slug"] for card in view["skills"]}
        assert SLUG not in rest and OTHER not in rest
        assert len(rest) + 2 == len(catalogue.all_skills())
        assert view["divisions"] and view["attributions"]


@pytest.mark.asyncio
class TestWhatTheBotIsTaught:
    async def test_the_prompt_carries_every_skill_on_that_bot(
        self, async_session, db_session
    ):
        _, org, bots = await _account(async_session, slug="prompt")
        await shelf.set_bots(org.id, SLUG, [bots[0].id])
        await shelf.set_bots(org.id, OTHER, [bots[0].id])
        block = await shelf.prompt_for_workflow(org.id, bots[0].id)
        assert f'<skill name="{SLUG}">' in block
        assert f'<skill name="{OTHER}">' in block
        # A bot nobody taught carries nothing rather than an empty heading.
        assert await shelf.prompt_for_workflow(org.id, bots[1].id) == ""

    async def test_a_database_that_cannot_be_read_is_no_skill_not_no_reply(
        self, async_session, db_session, monkeypatch
    ):
        _, org, bots = await _account(async_session, slug="down")

        async def boom(**_kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr(db_session, "list_organisation_skills", boom)
        assert await shelf.prompt_for_workflow(org.id, bots[0].id) == ""
