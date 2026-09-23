"""D-1b: "owner/repo, install them" -- skills from a repository link.

The repository is one tarball read in memory; a skill file is recognised
by its frontmatter with the name read leniently (published role packs put
a title where the spec wants a slug); a pack folder is reported, not
installed from a thread; what could not be read is named, never dropped.
Installing is a card, and an installed skill sits on the shelf, resolves
into a bot's prompt and uninstalls like a shipped one.
"""

from __future__ import annotations

import io
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api import constants
from api.db.models import OrganizationModel
from api.services.skills import imports, shelf
from api.services.skills.document import MAX_BODY_LINES
from api.services.workflow import actions, decibyl, skill_context

ROLE = """---
name: Incident Response Commander
description: Expert incident commander for production incidents.
color: "#e63946"
emoji: 🚨
---

# Incident Response Commander Agent

You are **Incident Response Commander**. Coordinate the response.
"""

GOOD = """---
name: brand-voice
description: Build a writing style profile from real posts.
license: MIT
---

Build a durable voice profile from real source material.
"""

NO_DESCRIPTION = """---
name: quiet-one
---

Nothing tells an agent when to use this.
"""

PACK = """---
name: front-desk-clinic
description: Answers the phone.
decibyl:
  format: 1
  pack: {}
---

## Greeting
Hello.
"""


def _tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(name=f"repo-abc123/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


REPO = {
    "README.md": b"# A pack of agents\n",
    "engineering/engineering-incident-response-commander.md": ROLE.encode(),
    "marketing/brand-voice.md": GOOD.encode(),
    "misc/quiet-one.md": NO_DESCRIPTION.encode(),
    "misc/too-long.md": (
        "---\nname: too-long\ndescription: long\n---\n"
        + "line\n" * (MAX_BODY_LINES + 1)
    ).encode(),
    "packs/front_desk_clinic/SKILL.md": PACK.encode(),
    "logo.png": b"\x89PNG",
}


def _serving(files: dict[str, bytes], *, status: int = 200):
    blob = _tarball(files)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "codeload.github.com"
        return httpx.Response(status, content=blob)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestTheLink:
    @pytest.mark.parametrize(
        "text, expected",
        [
            (
                "msitarzewski/agency-agents",
                ("msitarzewski", "agency-agents", "HEAD", ""),
            ),
            ("https://github.com/o/r", ("o", "r", "HEAD", "")),
            ("https://github.com/o/r.git", ("o", "r", "HEAD", "")),
            ("github.com/o/r/tree/v1.2/skills/ops", ("o", "r", "v1.2", "skills/ops")),
        ],
    )
    def test_the_shapes_people_paste(self, text, expected):
        link = imports.parse_link(text)
        assert (link.owner, link.repo, link.ref, link.path) == expected
        assert link.archive_url.startswith("https://codeload.github.com/")

    @pytest.mark.parametrize("text", ["", "https://gitlab.com/o/r", "just words", "o"])
    def test_anything_else_is_refused_by_name(self, text):
        with pytest.raises(imports.ImportError_):
            imports.parse_link(text)


@pytest.mark.asyncio
class TestFetchAndRecognise:
    async def test_the_tarball_is_read_and_the_shapes_told_apart(self):
        link = imports.parse_link("o/r")
        async with _serving(REPO) as client:
            fetched = await imports.fetch(link, client=client)
        assert fetched.skipped_binary == 1
        assert "README.md" in fetched.files
        found = imports.recognise(fetched)
        assert [s.slug for s in found.skills] == [
            "engineering-incident-response-commander",
            "brand-voice",
        ]
        commander = found.skills[0]
        assert commander.title == "Incident Response Commander"
        assert commander.skill.metadata["emoji"] == "🚨"
        assert found.packs == ["packs/front_desk_clinic"]
        skipped = dict(found.skipped)
        assert (
            "misc/quiet-one.md" in skipped
            and "description" in skipped["misc/quiet-one.md"]
        )
        assert "misc/too-long.md" in skipped and "lines" in skipped["misc/too-long.md"]

    async def test_a_folder_in_the_link_narrows_the_read(self):
        link = imports.parse_link("https://github.com/o/r/tree/main/marketing")
        async with _serving(REPO) as client:
            fetched = await imports.fetch(link, client=client)
        assert list(fetched.files) == ["marketing/brand-voice.md"]

    async def test_a_missing_repository_is_said(self):
        link = imports.parse_link("o/nope")
        async with _serving({}, status=404) as client:
            with pytest.raises(imports.ImportError_) as refused:
                await imports.fetch(link, client=client)
        assert "not found" in str(refused.value)


class _Borrow:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
class TestInstalled:
    async def test_it_sits_on_the_shelf_resolves_for_a_bot_and_uninstalls(
        self, async_session, monkeypatch
    ):
        from api.db import db_client

        org = OrganizationModel(provider_id="org-import", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        link = imports.parse_link("o/r")
        async with _serving(REPO) as client:
            found = imports.recognise(await imports.fetch(link, client=client))

        done = await imports.install(
            organization_id=org.id, user_id=None, link=link, found=found
        )
        assert done == ["engineering-incident-response-commander", "brand-voice"]

        rows = await db_client.list_skill_documents(organization_id=org.id)
        by = {r.slug: r for r in rows}
        assert by["brand-voice"].licence == "MIT"
        assert by["brand-voice"].source_repo == "o/r"
        assert "voice profile" in by["brand-voice"].body

        # On the shelf, under Installed, marked as imported.
        with patch.object(shelf, "_bot_names", AsyncMock(return_value={})):
            listing = await shelf.shelf(org.id)
        installed = {c["slug"]: c for c in listing["installed"]}
        assert installed["brand-voice"]["division"] == "Imported"
        assert installed["brand-voice"]["source"] == "o/r"

        # Resolves into what a bot reads, body and all.
        entries = await skill_context.installed_for(org.id)
        assert {e.slug for e in entries} >= {"brand-voice"}
        block = skill_context.block(
            entries, [e for e in entries if e.slug == "brand-voice"]
        )
        assert "voice profile" in block

        # Importing again is the newer version of itself, not a second row.
        await imports.install(
            organization_id=org.id, user_id=None, link=link, found=found
        )
        assert len(await db_client.list_skill_documents(organization_id=org.id)) == 2

        assert await imports.uninstall(organization_id=org.id, slugs=done) == 2
        assert await db_client.list_skill_documents(organization_id=org.id) == []
        assert await shelf.installed(org.id) == {}

    async def test_only_named_skills_install(self, async_session, monkeypatch):
        from api.db import db_client

        org = OrganizationModel(provider_id="org-only", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        link = imports.parse_link("o/r")
        async with _serving(REPO) as client:
            found = imports.recognise(await imports.fetch(link, client=client))
        done = await imports.install(
            organization_id=org.id,
            user_id=None,
            link=link,
            found=found,
            only=["brand-voice"],
        )
        assert done == ["brand-voice"]


@pytest.mark.asyncio
class TestTheCard:
    async def test_the_tool_reads_then_proposes(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", True)
        link = imports.parse_link("o/r")
        async with _serving(REPO) as client:
            fetched = await imports.fetch(link, client=client)
        with (
            patch.object(imports, "fetch", AsyncMock(return_value=fetched)),
            patch.object(
                actions,
                "propose",
                AsyncMock(return_value={"status": "proposed", "note": "ok"}),
            ) as propose,
        ):
            out = await imports.for_thread(7, {"repository": "o/r"})
        assert out["status"] == "proposed"
        assert out["found"].startswith("2 skills, 1 pack folder")
        assert len(out["skipped"]) == 2
        args = propose.await_args.kwargs["arguments"]
        assert args["action"] == actions.INSTALL_FROM_REPOSITORY
        assert args["slugs"] == [
            "engineering-incident-response-commander",
            "brand-voice",
        ]

    async def test_nothing_to_install_is_said_not_proposed(self, monkeypatch):
        link = imports.parse_link("o/r")
        async with _serving({"README.md": b"# hi\n"}) as client:
            fetched = await imports.fetch(link, client=client)
        with (
            patch.object(imports, "fetch", AsyncMock(return_value=fetched)),
            patch.object(actions, "propose", AsyncMock()) as propose,
        ):
            out = await imports.for_thread(7, {"repository": "o/r"})
        assert out["status"] == "nothing_to_install"
        propose.assert_not_awaited()

    async def test_the_card_names_the_count_and_is_reversible(self):
        payload = await actions.resolve(
            organization_id=7,
            workflow_id=None,
            arguments={
                "action": actions.INSTALL_FROM_REPOSITORY,
                "repository": "o/r",
                "slugs": ["a", "b"],
                "titles": ["A", "B"],
            },
        )
        assert payload["label"] == "Install 2 skills from o/r"
        assert payload["reversible"] is True
        assert "review" in payload["effect"]

    async def test_confirming_fetches_again_and_installs(self, monkeypatch):
        link = imports.parse_link("o/r")
        async with _serving(REPO) as client:
            fetched = await imports.fetch(link, client=client)
        with (
            patch.object(imports, "fetch", AsyncMock(return_value=fetched)),
            patch.object(
                imports, "install", AsyncMock(return_value=["brand-voice"])
            ) as install,
        ):
            payload = {
                "action": actions.INSTALL_FROM_REPOSITORY,
                "args": {
                    "repository": "o/r",
                    "ref": "HEAD",
                    "path": "",
                    "slugs": ["brand-voice"],
                },
                "confirmed": {"by": 3},
            }
            line = await actions._execute(7, payload)
        assert line.startswith("Installed 1 skill from o/r")
        assert install.await_args.kwargs["only"] == ["brand-voice"]
        assert install.await_args.kwargs["user_id"] == 3
        assert payload["result"]["installed"] == ["brand-voice"]

    async def test_undo_takes_them_off_again(self):
        with patch.object(imports, "uninstall", AsyncMock(return_value=1)) as undo:
            await actions._reverse(
                7,
                {
                    "action": actions.INSTALL_FROM_REPOSITORY,
                    "args": {"slugs": ["a"]},
                    "result": {"installed": ["brand-voice"]},
                },
            )
        assert undo.await_args.kwargs["slugs"] == ["brand-voice"]


class TestTheDrawer:
    def test_the_tool_is_offered_with_the_others_and_named_in_the_rules(
        self, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", True)
        names = {t["name"] for t in decibyl.office_tools()}
        assert imports.TOOL_NAME in names
        assert imports.TOOL_NAME in decibyl.SYSTEM

    def test_it_is_a_write_that_ends_the_tool_phase(self):
        call = SimpleNamespace(name=imports.TOOL_NAME, arguments={})
        assert not decibyl._was_a_read(call, {"status": "proposed"})
