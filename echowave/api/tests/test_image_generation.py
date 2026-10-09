"""Image generation end to end, against a fake provider and a fake bucket.

Held here: the flag is off by default and every route is a 404 while it is;
the key goes into the workspace's vault and never comes back out; nothing
crosses from one workspace to another (keys, the choice, images, references);
a poster never carries a fact the person did not give, and nothing is spent
on one that would; with no provider the card goes on the thread instead; and
every image is metered -- the vendor's cost beside a customer charge of zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api import constants
from api.db.image_models import GeneratedImageModel
from api.db.models import OrganizationModel, OrganizationProviderCredentialModel
from api.enums import AgentEventKind, CostComponent, OrganizationRole
from api.services import features
from api.services.billing.events import INCLUDED, TIMELINE_PRICES
from api.services.billing.markup import COMPONENT_MARKUP_BPS
from api.services.images import guard, keys, metering, registry, service, store, tools
from api.services.images.providers.base import (
    ImageProviderError,
    KeyCheck,
    MadeImage,
    ProviderResult,
)
from api.services.workflow import agent_timeline, decibyl

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x03"
    b"\x08\x06\x00\x00\x00" + b"\x00" * 16
)
KEY = "AIzaSy-test-key-not-real-0000abcd"
SAID = "Make a Diwali sale poster for Narayani Sweets, 20% off, call 98765 43210"
BRIEF = {
    "business_name": "Narayani Sweets",
    "headline": "Diwali Sale",
    "lines": ["20% off", "Call 98765 43210"],
    "language": "English",
    "format": "instagram_square",
    "look": "festive, diyas, our blue",
}


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "IMAGE_GENERATION_ENABLED", True)


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(constants, "IMAGE_GENERATION_ENABLED", False)


@pytest.fixture(autouse=True)
def no_platform_keys(monkeypatch):
    monkeypatch.setattr(constants, "IMAGE_GEMINI_API_KEY", "")
    monkeypatch.setattr(constants, "IMAGE_OPENAI_API_KEY", "")
    monkeypatch.setattr(constants, "IMAGE_BEDROCK_ENABLED", False)


class FakeFS:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def acreate_file_from_bytes(self, key, data):
        self.files[key] = data
        return True

    async def aread_bytes(self, key, max_bytes):
        return self.files.get(key)

    async def aget_signed_url(self, key, expiration=3600, **_):
        return f"https://bucket.test/{key}"


@pytest.fixture
def bucket(monkeypatch):
    fs = FakeFS()
    monkeypatch.setattr(store, "_fs", lambda backend=None: fs)
    return fs


class FakeProvider:
    name = "google"
    label = "Gemini"
    takes_references = True
    max_count = 4

    def __init__(self, *, fail: ImageProviderError | None = None, check="verified"):
        self.fail = fail
        self.check = check
        self.requests = []
        self.keys = []

    def model(self):
        return "gemini-test-model"

    async def generate(self, request, *, api_key, own_key=True):
        self.requests.append(request)
        self.keys.append(api_key)
        if self.fail:
            raise self.fail
        return ProviderResult(
            images=[
                MadeImage(data=PNG, mime_type="image/png", width=2, height=3)
                for _ in range(request.count)
            ],
            model=self.model(),
            usage={"requests": request.count},
        )

    async def check_key(self, api_key):
        return KeyCheck(self.check, f"check said {self.check}")


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setitem(registry.PROVIDERS, "google", provider)
    return provider


@pytest.fixture
def timeline(monkeypatch):
    rows: list[dict] = []

    async def record(**kwargs):
        rows.append(kwargs)
        return len(rows)

    monkeypatch.setattr(agent_timeline, "record", record)
    return rows


async def _org(session, name: str) -> int:
    org = OrganizationModel(
        provider_id=f"img-{name}-{datetime.now(UTC).timestamp()}",
        quota_decibyl_tokens=0,
    )
    session.add(org)
    await session.flush()
    return org.id


async def _user(db_session, org_id: int, role: str, name: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(
        f"img-{name}-{datetime.now(UTC).timestamp()}"
    )
    user.selected_organization_id = org_id
    await db_session.add_user_to_organization(user.id, org_id, role=role)
    return user


class TestTheFlag:
    def test_registered_described_and_off_by_default(self):
        assert features.FLAGS["image_generation"] == "IMAGE_GENERATION_ENABLED"
        assert features.DESCRIPTIONS["image_generation"]
        assert constants.IMAGE_GENERATION_ENABLED is False

    def test_platform_keys_are_their_own_settings(self):
        # Never the model keys a deployment already holds: images stay on
        # the workspace's own key unless somebody decides otherwise.
        import inspect

        source = inspect.getsource(constants)
        assert 'os.getenv("IMAGE_GEMINI_API_KEY")' in source
        assert 'os.getenv("IMAGE_OPENAI_API_KEY")' in source
        assert all(not registry.platform_ready(n) for n in registry.names())

    def test_off_decibyl_has_no_tool_and_no_rule(self, off):
        names = {t["name"] for t in decibyl.office_tools()}
        assert tools.TOOL_NAME not in names
        assert tools.TOOL_NAME not in decibyl.system_prompt()

    def test_on_decibyl_has_the_tool_with_its_rule_and_the_cards(self, on):
        names = {t["name"] for t in decibyl.office_tools()}
        assert tools.TOOL_NAME in names
        assert tools.TOOL_NAME in decibyl.system_prompt()
        kinds = decibyl.thread_filter()["kinds"]
        assert AgentEventKind.IMAGE_PROVIDER_OFFERED.value in kinds
        assert AgentEventKind.IMAGES_MADE.value in kinds

    def test_the_templates_and_packs_wait_on_the_flag(self, off):
        from api.services.agent_templates.catalogue import get_template

        for slug in ("poster_designer", "ad_creative_maker"):
            template = get_template(slug)
            assert template.needs_images
            assert template.requires_feature == "image_generation"
            assert not template.available

    def test_on_the_templates_are_on_the_shelf(self, on):
        from api.services.agent_templates.catalogue import get_template
        from api.services.packs import creative
        from api.services.packs.catalogue import DECIBYL, all_packs

        for slug in ("poster_designer", "ad_creative_maker"):
            assert get_template(slug).available
        # Built now, with the flag on: listed, and in the catalogue.
        built = {p.template_id: p for p in creative.packs(DECIBYL)}
        assert set(built) == {"poster_designer", "ad_creative_maker"}
        assert all(p.listed for p in built.values())
        assert set(built) <= {p.template_id for p in all_packs()}

    def test_off_the_packs_are_off_the_shelf(self, off):
        from api.services.packs import creative
        from api.services.packs.catalogue import DECIBYL

        assert not any(p.listed for p in creative.packs(DECIBYL))

    def test_a_hire_marks_the_bot_as_making_images(self):
        from api.services.agent_templates import equip
        from api.services.agent_templates.catalogue import get_template

        configurations = equip.configurations(
            None, template=get_template("poster_designer")
        )
        assert tools.wants_images(configurations)
        assert not tools.wants_images({tools.CONFIG_KEY: "yes"})


class TestMetering:
    def test_free_to_the_customer_and_never_a_line_on_the_timeline(self):
        assert COMPONENT_MARKUP_BPS[CostComponent.IMAGE.value] == 0
        assert metering.CUSTOMER_PAISE_PER_IMAGE == 0
        assert TIMELINE_PRICES[AgentEventKind.IMAGES_MADE.value] == INCLUDED
        assert TIMELINE_PRICES[AgentEventKind.IMAGE_PROVIDER_OFFERED.value] == INCLUDED

    @pytest.mark.asyncio
    async def test_the_book_figure_when_no_rate_is_on_file(self, db_session):
        paise, source = await metering.vendor_paise_per_image(
            "google", constants.IMAGE_GEMINI_MODEL
        )
        assert source == metering.DEFAULT_BOOK
        assert paise > 0

    @pytest.mark.asyncio
    async def test_an_overridden_model_is_costed_at_the_providers_figure(
        self, db_session
    ):
        exact, _ = await metering.vendor_paise_per_image(
            "openai", constants.IMAGE_OPENAI_MODEL
        )
        other, source = await metering.vendor_paise_per_image("openai", "renamed")
        assert source == metering.DEFAULT_BOOK
        assert other == exact

    @pytest.mark.asyncio
    async def test_an_unknown_vendor_is_said_uncosted_not_zero_by_accident(
        self, db_session
    ):
        assert await metering.vendor_paise_per_image("nobody", "x") == (
            0,
            metering.NONE,
        )

    @pytest.mark.asyncio
    async def test_a_rate_on_the_card_wins(self, monkeypatch):
        from types import SimpleNamespace

        from api.enums import RateUnit

        async def resolve(session, **kwargs):
            assert kwargs["component"] == CostComponent.IMAGE
            return SimpleNamespace(rate_mpaise=500_000, unit=RateUnit.IMAGE)

        monkeypatch.setattr(metering, "resolve_provider_rate", resolve)
        assert await metering.vendor_paise_per_image("google", "m") == (
            500,
            metering.RATE_CARD,
        )


class TestTheGuard:
    def test_given_facts_pass(self):
        assert guard.check(guard.brief_from(BRIEF), said=SAID) == []

    def test_an_invented_price_is_refused(self):
        brief = guard.brief_from({**BRIEF, "lines": ["Only ₹499"]})
        problems = guard.check(brief, said=SAID)
        assert any("499" in p for p in problems)

    def test_an_invented_claim_and_address_are_refused(self):
        brief = guard.brief_from(
            {**BRIEF, "lines": ["Best sweets in town", "www.narayani.in"]}
        )
        problems = guard.check(brief, said=SAID)
        assert any("best" in p for p in problems)
        assert any("narayani.in" in p for p in problems)

    def test_text_smuggled_into_the_look_is_refused(self):
        brief = guard.brief_from({**BRIEF, "look": "with 50% OFF in gold"})
        assert any("look" in p for p in guard.check(brief, said=SAID))

    def test_indic_digits_are_the_numbers_the_person_said(self):
        brief = guard.brief_from({**BRIEF, "lines": ["२०% छूट"]})
        assert guard.check(brief, said=SAID) == []


@pytest.mark.asyncio
class TestKeys:
    async def test_a_key_goes_into_the_vault_and_only_its_tail_comes_back(
        self, on, fake, db_session, async_session
    ):
        org = await _org(async_session, "vault")
        answer = await keys.connect(org, user_id=None, provider="google", api_key=KEY)
        assert KEY not in repr(answer)
        google = next(p for p in answer["providers"] if p["provider"] == "google")
        assert google["ready"] and google["source"] == "your_key"
        assert google["masked_key"].endswith("abcd")
        assert answer["chosen"] == "google" and answer["ready"] is True
        assert answer["verification"] == "verified"
        row = await async_session.scalar(
            select(OrganizationProviderCredentialModel).where(
                OrganizationProviderCredentialModel.organization_id == org,
                OrganizationProviderCredentialModel.component == "image",
            )
        )
        assert row is not None and row.provider == "google"
        assert KEY not in row.encrypted_key
        resolved = await keys.resolve(org)
        assert resolved.api_key == KEY and resolved.key_source == "byok"

    async def test_a_refused_key_is_not_stored(
        self, on, fake, db_session, async_session
    ):
        fake.check = "rejected"
        org = await _org(async_session, "refused")
        with pytest.raises(keys.ImageKeyError):
            await keys.connect(org, user_id=None, provider="google", api_key=KEY)
        assert await keys.resolve(org, "google") is None
        assert await keys.chosen(org) is None

    async def test_no_key_and_nothing_ready_asks_for_one(
        self, on, fake, db_session, async_session
    ):
        org = await _org(async_session, "nokey")
        with pytest.raises(keys.ImageKeyError, match="Paste your Gemini API key"):
            await keys.connect(org, user_id=None, provider="google", api_key=None)

    async def test_an_unknown_provider_is_refused(self, on, db_session, async_session):
        org = await _org(async_session, "unknown")
        with pytest.raises(keys.ImageKeyError):
            await keys.connect(org, user_id=None, provider="midjourney", api_key=KEY)

    async def test_one_workspaces_key_and_choice_are_not_anothers(
        self, on, fake, db_session, async_session
    ):
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        await keys.connect(mine, user_id=None, provider="google", api_key=KEY)
        assert await keys.chosen(theirs) is None
        assert await keys.resolve(theirs, "google") is None
        status = await keys.status(theirs)
        assert not any(p["ready"] for p in status["providers"])
        assert all(p["masked_key"] is None for p in status["providers"])


@pytest.mark.asyncio
class TestGenerate:
    async def _connected(self, async_session, fake, name="gen") -> int:
        org = await _org(async_session, name)
        await keys.connect(org, user_id=None, provider="google", api_key=KEY)
        fake.requests.clear()
        fake.keys.clear()
        return org

    async def test_off_nothing_happens(self, off, fake, db_session, async_session):
        org = await _org(async_session, "off")
        result = await tools.run(org, arguments=BRIEF, said=SAID)
        assert result["status"] == "unavailable"
        assert fake.requests == []

    async def test_an_invented_fact_spends_nothing(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await self._connected(async_session, fake, "invented")
        result = await tools.run(
            org, arguments={**BRIEF, "lines": ["Only ₹499"]}, said=SAID
        )
        assert result["status"] == "needs_facts"
        assert fake.requests == [] and bucket.files == {} and timeline == []
        assert tools.keeps_tools(result)

    async def test_no_provider_puts_the_card_on_the_thread(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await _org(async_session, "card")
        result = await tools.run(org, arguments=BRIEF, said=SAID, workflow_id=None)
        assert result["status"] == "needs_provider"
        assert "Settings" in result["note"]  # told never to send anyone there
        assert fake.requests == []
        [card] = timeline
        assert card["kind"] == AgentEventKind.IMAGE_PROVIDER_OFFERED.value
        assert card["organization_id"] == org
        assert card["payload"]["request"] == SAID
        assert not tools.keeps_tools(result)

    async def test_made_stored_metered_and_shown_on_one_card(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await self._connected(async_session, fake, "made")
        result = await tools.run(
            org, arguments={**BRIEF, "options": 3}, said=SAID, user_id=None
        )
        assert result["status"] == "made"
        assert len(result["images"]) == 3
        assert fake.keys == [KEY]
        # The provider got a prompt we composed, carrying the given text only.
        prompt = fake.requests[0].prompt
        assert "Narayani Sweets" in prompt and "20% off" in prompt
        assert guard.NOTHING_ELSE in prompt

        rows = (
            await async_session.scalars(
                select(GeneratedImageModel).where(
                    GeneratedImageModel.organization_id == org
                )
            )
        ).all()
        assert len(rows) == 3
        assert {r.request_id for r in rows} == {rows[0].request_id}
        for row in rows:
            assert row.storage_key.startswith(f"images/{org}/")
            assert bucket.files[row.storage_key] == PNG
            assert row.key_source == "byok" and row.provider == "google"
            assert row.charged_paise == 0
            assert row.vendor_cost_paise > 0
            assert row.cost_source == metering.DEFAULT_BOOK
            assert row.spec["headline"] == "Diwali Sale"
        [card] = timeline
        assert card["kind"] == AgentEventKind.IMAGES_MADE.value
        assert [i["image_uuid"] for i in card["payload"]["images"]] == [
            r["image_id"] for r in result["images"]
        ]
        # Ids, never a storage key or URL, on the row the thread reads.
        assert "storage_key" not in repr(card["payload"])
        assert "bucket.test" not in repr(card["payload"])

    async def test_an_edit_keeps_the_approved_facts_and_names_its_parent(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await self._connected(async_session, fake, "edit")
        made = await tools.run(org, arguments=BRIEF, said=SAID)
        first = made["images"][0]["image_id"]
        # A later turn: the person's words no longer carry the price, but
        # the brief they approved does.
        edited = await tools.run(
            org,
            arguments={"edit_image_id": first, "edit_instruction": "make it bigger"},
            said="make the headline bigger",
        )
        assert edited["status"] == "made" and len(edited["images"]) == 1
        row = await store.get(org, edited["images"][0]["image_id"])
        assert row.parent_uuid == first
        assert fake.requests[-1].base is not None

    async def test_another_workspaces_image_cannot_be_edited_or_attached(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        mine = await self._connected(async_session, fake, "mine2")
        theirs = await _org(async_session, "theirs2")
        await keys.connect(theirs, user_id=None, provider="google", api_key=KEY)
        made = await tools.run(mine, arguments=BRIEF, said=SAID)
        image_id = made["images"][0]["image_id"]
        fake.requests.clear()

        edit = await tools.run(
            theirs,
            arguments={"edit_image_id": image_id, "edit_instruction": "bigger"},
            said=SAID,
        )
        attach = await tools.run(
            theirs, arguments={**BRIEF, "reference_image_ids": [image_id]}, said=SAID
        )
        assert edit["status"] == "not_found"
        assert attach["status"] == "not_found"
        assert fake.requests == []
        assert await store.get(theirs, image_id) is None
        assert await store.get_many(theirs, [image_id]) == []
        assert await service.attachment_for(theirs, image_id) is None

    async def test_a_reference_that_cannot_be_read_is_said(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await self._connected(async_session, fake, "ref")
        ref = await service.add_reference(
            organization_id=org, user_id=None, data=PNG, filename="logo.png"
        )
        bucket.files.clear()
        result = await tools.run(
            org,
            arguments={**BRIEF, "reference_image_ids": [ref["image_uuid"]]},
            said=SAID,
        )
        assert result["status"] == "error"
        assert "logo.png" in result["error"]
        assert fake.requests == []

    async def test_a_refused_own_key_brings_the_card_back_with_why(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await self._connected(async_session, fake, "refusedrun")
        fake.fail = ImageProviderError("auth", "Your Gemini key was refused.")
        result = await tools.run(org, arguments=BRIEF, said=SAID)
        assert result["status"] == "needs_provider"
        [card] = timeline
        assert card["kind"] == AgentEventKind.IMAGE_PROVIDER_OFFERED.value
        assert card["payload"]["reason"] == "Your Gemini key was refused."
        assert card["payload"]["provider"] == "google"
        assert len(fake.requests) == 1  # never retried

    async def test_a_vendor_failure_is_said_and_not_retried(
        self, on, fake, bucket, timeline, db_session, async_session
    ):
        org = await self._connected(async_session, fake, "fail")
        fake.fail = ImageProviderError("timeout", "Gemini took too long.")
        result = await tools.run(org, arguments=BRIEF, said=SAID)
        assert result == {
            "status": "error",
            "error": "Gemini took too long.",
            "note": "Say what happened in one line. Nothing was retried.",
        }
        assert len(fake.requests) == 1 and timeline == []


@pytest.mark.asyncio
class TestRoutes:
    async def test_off_every_route_is_a_404(
        self, off, test_client_factory, db_session, async_session
    ):
        org = await _org(async_session, "routes-off")
        admin = await _user(db_session, org, OrganizationRole.ADMIN.value, "off")
        async with test_client_factory(admin) as client:
            answers = [
                await client.get("/api/v1/images/providers"),
                await client.put(
                    "/api/v1/images/provider", json={"provider": "google"}
                ),
                await client.get(f"/api/v1/images/img_{'0' * 32}"),
            ]
        assert [a.status_code for a in answers] == [404, 404, 404]

    async def test_the_card_round_trip_never_echoes_the_key(
        self, on, fake, test_client_factory, db_session, async_session
    ):
        org = await _org(async_session, "routes-on")
        admin = await _user(db_session, org, OrganizationRole.ADMIN.value, "admin")
        member = await _user(db_session, org, OrganizationRole.MEMBER.value, "member")
        async with test_client_factory(member) as client:
            listed = await client.get("/api/v1/images/providers")
            refused = await client.put(
                "/api/v1/images/provider", json={"provider": "google", "api_key": KEY}
            )
        assert listed.status_code == 200
        assert [p["provider"] for p in listed.json()["providers"]] == [
            "google",
            "openai",
            "aws_bedrock",
        ]
        assert refused.status_code == 403
        assert await keys.resolve(org, "google") is None

        async with test_client_factory(admin) as client:
            connected = await client.put(
                "/api/v1/images/provider", json={"provider": "google", "api_key": KEY}
            )
            bad = await client.put(
                "/api/v1/images/provider", json={"provider": "nope", "api_key": KEY}
            )
        assert connected.status_code == 200
        assert KEY not in connected.text
        assert connected.json()["chosen"] == "google"
        assert bad.status_code == 400 and KEY not in bad.text

    async def test_images_are_served_to_their_own_workspace_only(
        self, on, bucket, test_client_factory, db_session, async_session
    ):
        mine = await _org(async_session, "img-mine")
        theirs = await _org(async_session, "img-theirs")
        me = await _user(db_session, mine, OrganizationRole.MEMBER.value, "me")
        other = await _user(db_session, theirs, OrganizationRole.MEMBER.value, "other")
        async with test_client_factory(me) as client:
            uploaded = await client.post(
                "/api/v1/images/references",
                files={"file": ("logo.png", PNG, "image/png")},
            )
            not_an_image = await client.post(
                "/api/v1/images/references",
                files={"file": ("notes.png", b"not an image", "image/png")},
            )
            image_id = uploaded.json()["image_uuid"]
            shown = await client.get(f"/api/v1/images/{image_id}")
            downloaded = await client.get(f"/api/v1/images/{image_id}/file")
        assert uploaded.status_code == 200 and image_id.startswith("img_")
        assert not_an_image.status_code == 400
        assert shown.json()["url"].startswith(f"https://bucket.test/images/{mine}/")
        assert "storage_key" not in shown.json()["image"]
        assert downloaded.content == PNG
        assert downloaded.headers["content-type"] == "image/png"

        async with test_client_factory(other) as client:
            stolen = await client.get(f"/api/v1/images/{image_id}")
            stolen_file = await client.get(f"/api/v1/images/{image_id}/file")
        assert stolen.status_code == 404
        assert stolen_file.status_code == 404
