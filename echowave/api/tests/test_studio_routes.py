"""Studio against the real database: tenancy, builds, the preview, putting
agents on a site, and one whole chat turn that creates a site and builds it.

The sandbox is replaced with a stub that returns a built tree; the real build
is exercised by hand (sandbox/README.md) because it needs Docker and npm.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from api import constants
from api.db.models import OrganizationModel, UserModel
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.agent_builder.settings import BuilderModel
from api.services.studio import session as studio_session
from api.services.studio import sites

INDEX = b"<!doctype html><html><body><div id=root></div></body></html>"
ASSET = b"console.log('hi')"


def _built(files=None):
    files = files or {"index.html": INDEX, "assets/index-abc.js": ASSET}
    return {
        "exit_code": 0,
        "timed_out": False,
        "seconds": 3.2,
        "log": "vite build\n✓ built in 300ms",
        "files": {k: base64.b64encode(v).decode() for k, v in files.items()},
    }


FAILED = {
    "exit_code": 1,
    "timed_out": False,
    "seconds": 1.0,
    "log": 'error during build: src/App.jsx (3:10): Expected "}"\n    at x (y)',
    "files": {},
}


@pytest.fixture
def studio_on(monkeypatch):
    monkeypatch.setattr(constants, "STUDIO_ENABLED", True)
    monkeypatch.setattr(constants, "SANDBOX_URL", "http://sandbox.test")


@pytest.fixture
def sandbox(monkeypatch):
    """Each build returns the next queued result; records what was sent."""
    queue: list[dict] = []
    sent: list[dict] = []

    async def fake(site):
        sent.append(dict(site.files))
        return queue.pop(0)

    monkeypatch.setattr(sites, "_request_build", fake)
    return queue, sent


async def _account(async_session, slug):
    org = OrganizationModel(provider_id=f"org-studio-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"user-studio-{slug}",
        email=f"{slug}@studio.example",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()
    return org, user


# --- the flag and tenancy ----------------------------------------------------


async def test_studio_is_a_404_while_switched_off(
    monkeypatch, db_session, async_session, test_client_factory
):
    monkeypatch.setattr(constants, "STUDIO_ENABLED", False)
    _, user = await _account(async_session, "off")
    async with test_client_factory(user) as client:
        assert (await client.get("/api/v1/studio/sites")).status_code == 404


async def test_one_workspace_cannot_reach_anothers_site(
    studio_on, db_session, async_session, test_client_factory
):
    _, owner = await _account(async_session, "owner")
    _, other = await _account(async_session, "other")
    async with test_client_factory(owner) as client:
        created = await client.post("/api/v1/studio/sites", json={"name": "Clinic"})
        assert created.status_code == 200
        site_id = created.json()["id"]
        assert [
            s["id"] for s in (await client.get("/api/v1/studio/sites")).json()["sites"]
        ] == [site_id]

    async with test_client_factory(other) as client:
        assert (await client.get("/api/v1/studio/sites")).json()["sites"] == []
        for method, path in (
            ("get", f"/api/v1/studio/sites/{site_id}"),
            ("get", f"/api/v1/studio/sites/{site_id}/file?path=package.json"),
            ("post", f"/api/v1/studio/sites/{site_id}/build"),
            ("get", f"/api/v1/studio/sites/{site_id}/download"),
            ("delete", f"/api/v1/studio/sites/{site_id}"),
        ):
            response = await getattr(client, method)(path)
            assert response.status_code == 404, (method, path)
        response = await client.put(
            f"/api/v1/studio/sites/{site_id}/files",
            json={"files": [{"path": "src/x.js", "content": "x"}]},
        )
        assert response.status_code == 404

    # Still there, unchanged, for its owner.
    async with test_client_factory(owner) as client:
        body = (await client.get(f"/api/v1/studio/sites/{site_id}")).json()
        assert "src/x.js" not in [f["path"] for f in body["files"]]


# --- files, builds and the preview ------------------------------------------


async def test_files_build_and_preview(
    studio_on, sandbox, db_session, async_session, test_client_factory
):
    queue, sent = sandbox
    _, user = await _account(async_session, "build")
    async with test_client_factory(user) as client:
        site_id = (
            await client.post("/api/v1/studio/sites", json={"name": "Bakery"})
        ).json()["id"]
        wrote = await client.put(
            f"/api/v1/studio/sites/{site_id}/files",
            json={"files": [{"path": "src/Menu.jsx", "content": "export default 1;"}]},
        )
        assert wrote.status_code == 200 and wrote.json()["written"] == ["src/Menu.jsx"]
        read = await client.get(
            f"/api/v1/studio/sites/{site_id}/file", params={"path": "src/Menu.jsx"}
        )
        assert read.json()["content"] == "export default 1;"

        queue.append(_built())
        built = await client.post(f"/api/v1/studio/sites/{site_id}/build")
        assert built.status_code == 200
        assert built.json()["status"] == "succeeded"
        assert "src/Menu.jsx" in sent[0]
        preview = built.json()["preview_url"]
        token = preview.rstrip("/").rsplit("/", 1)[-1]

        page = await client.get(f"/api/v1/public/sites/{token}/")
        assert page.status_code == 200 and page.content == INDEX
        assert page.headers["content-security-policy"].startswith("sandbox ")
        assert "allow-same-origin" not in page.headers["content-security-policy"]
        assert page.headers["x-content-type-options"] == "nosniff"

        asset = await client.get(f"/api/v1/public/sites/{token}/assets/index-abc.js")
        assert asset.content == ASSET
        assert asset.headers["content-type"].startswith(
            "text/javascript"
        ) or asset.headers["content-type"].startswith("application/javascript")
        assert asset.headers["access-control-allow-origin"] == "*"

        # A client-side route is the app; a missing file is not.
        route = await client.get(f"/api/v1/public/sites/{token}/about/team")
        assert route.content == INDEX
        missing = await client.get(f"/api/v1/public/sites/{token}/assets/gone.js")
        assert missing.status_code == 404
        assert (
            await client.get("/api/v1/public/sites/not-a-token/")
        ).status_code == 404

        # A failed rebuild reports why and leaves the last good preview up.
        queue.append(FAILED)
        failed = await client.post(f"/api/v1/studio/sites/{site_id}/build")
        assert failed.json()["status"] == "failed"
        assert "src/App.jsx (3:10)" in failed.json()["errors"]
        assert (await client.get(f"/api/v1/public/sites/{token}/")).content == INDEX

        download = await client.get(f"/api/v1/studio/sites/{site_id}/download")
        assert download.headers["content-type"] == "application/zip"
        assert (
            download.headers["content-disposition"]
            == 'attachment; filename="bakery.zip"'
        )


async def test_the_preview_follows_the_sites_own_workspace_flag(
    studio_on, sandbox, monkeypatch, db_session, async_session
):
    queue, _ = sandbox
    org, user = await _account(async_session, "flag")
    site = await sites.create_site(organization_id=org.id, user_id=user.id, name="S")
    queue.append(_built())
    site, _ = await sites.build_site(site, organization_id=org.id)
    monkeypatch.setattr(constants, "STUDIO_ENABLED", False)
    from httpx import ASGITransport, AsyncClient

    from api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        response = await c.get(f"/api/v1/public/sites/{site.preview_token}/")
    assert response.status_code == 404


async def test_a_preview_on_its_own_host_is_not_sandboxed(
    studio_on, sandbox, monkeypatch, db_session, async_session
):
    queue, _ = sandbox
    monkeypatch.setattr(constants, "SITE_PREVIEW_BASE_URL", "https://sites.example.com")
    org, user = await _account(async_session, "host")
    site = await sites.create_site(organization_id=org.id, user_id=user.id, name="S")
    queue.append(_built())
    site, _ = await sites.build_site(site, organization_id=org.id)
    assert sites.preview_url(site).startswith(
        "https://sites.example.com/api/v1/public/"
    )
    from httpx import ASGITransport, AsyncClient

    from api.app import app

    path = f"/api/v1/public/sites/{site.preview_token}/"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        own_host = await c.get(path, headers={"host": "sites.example.com"})
        api_host = await c.get(path, headers={"host": "api.example.com"})
    assert "content-security-policy" not in own_host.headers
    assert api_host.headers["content-security-policy"].startswith("sandbox ")


async def test_two_builds_of_one_site_cannot_run_at_once(
    studio_on, db_session, async_session
):
    org, user = await _account(async_session, "race")
    site = await sites.create_site(organization_id=org.id, user_id=user.id, name="S")
    now = datetime.now(UTC)
    claim = db_session.claim_site_build
    assert await claim(
        site.id, organization_id=org.id, stale_before=now - timedelta(minutes=20)
    )
    assert not await claim(
        site.id, organization_id=org.id, stale_before=now - timedelta(minutes=20)
    )
    with pytest.raises(sites.SiteError, match="already being built"):
        await sites.build_site(site, organization_id=org.id)
    # A build that has been "running" past the stale window died; reclaim it.
    assert await claim(
        site.id, organization_id=org.id, stale_before=now + timedelta(seconds=1)
    )


async def test_without_a_sandbox_a_build_is_refused_and_says_why(
    monkeypatch, db_session, async_session
):
    monkeypatch.setattr(constants, "SANDBOX_URL", None)
    org, user = await _account(async_session, "nosbx")
    site = await sites.create_site(organization_id=org.id, user_id=user.id, name="S")
    with pytest.raises(sites.SiteError, match="SANDBOX_URL"):
        await sites.build_site(site, organization_id=org.id)
    refreshed = await sites.get_site(site.id, organization_id=org.id)
    assert refreshed.build_status == "failed"


# --- agents on the site ------------------------------------------------------


async def test_agents_go_on_a_site_only_for_named_domains(
    studio_on, db_session, async_session
):
    org, user = await _account(async_session, "embed")
    other_org, other_user = await _account(async_session, "embed-other")
    site = await sites.create_site(organization_id=org.id, user_id=user.id, name="S")
    a = await db_session.create_workflow(
        "Reception", {"nodes": [], "edges": []}, user.id, org.id
    )
    b = await db_session.create_workflow(
        "Sales", {"nodes": [], "edges": []}, user.id, org.id
    )
    foreign = await db_session.create_workflow(
        "Theirs", {"nodes": [], "edges": []}, other_user.id, other_org.id
    )

    with pytest.raises(sites.SiteError, match="domain"):
        await sites.put_agents_on_site(
            site,
            organization_id=org.id,
            user_id=user.id,
            workflow_ids=[a.id],
            domains=[],
        )
    with pytest.raises(sites.SiteError, match=f"No agent {foreign.id}"):
        await sites.put_agents_on_site(
            site,
            organization_id=org.id,
            user_id=user.id,
            workflow_ids=[foreign.id],
            domains=["x.com"],
        )
    assert not await db_session.get_embed_tokens_by_workflow(foreign.id, other_org.id)

    out = await sites.put_agents_on_site(
        site,
        organization_id=org.id,
        user_id=user.id,
        workflow_ids=[a.id, b.id],
        domains=["https://Clinic.Example.com/", "clinic.example.com"],
    )
    assert out["domains"] == ["clinic.example.com"]
    assert [agent["name"] for agent in out["agents"]] == ["Reception", "Sales"]

    tokens = await db_session.get_embed_tokens_by_workflow(a.id, org.id)
    assert tokens[0].allowed_domains == ["clinic.example.com"]
    site = await sites.get_site(site.id, organization_id=org.id)
    assert site.agent_workflow_ids == [a.id, b.id]
    html = site.files["index.html"]
    assert html.count("decibyl-widget.js?token=") == 2
    assert tokens[0].token in html

    # Again, with a second domain: the same token, now valid on both.
    await sites.put_agents_on_site(
        site,
        organization_id=org.id,
        user_id=user.id,
        workflow_ids=[a.id],
        domains=["www.clinic.example.com"],
    )
    again = await db_session.get_embed_tokens_by_workflow(a.id, org.id)
    assert len(again) == 1 and again[0].token == tokens[0].token
    assert again[0].allowed_domains == ["clinic.example.com", "www.clinic.example.com"]
    site = await sites.get_site(site.id, organization_id=org.id)
    assert site.files["index.html"].count("decibyl-widget.js?token=") == 1


# --- one whole chat turn -----------------------------------------------------


async def test_one_message_creates_writes_and_builds_a_site(
    studio_on, sandbox, monkeypatch, db_session, async_session
):
    queue, sent = sandbox
    queue.extend([FAILED, _built()])
    org, user = await _account(async_session, "chat")
    app_code = "export default function App() { return <h1>Sunrise</h1>; }\n"

    script = iter(
        [
            ModelReply("", (ToolCall("1", "create_site", {"name": "Sunrise Dental"}),)),
            # site_id is filled in below from the create_site result.
            "WRITE",
            ModelReply("", (ToolCall("3", "build_site", {"site_id": None}),)),
            "FIX",
            ModelReply("", (ToolCall("5", "build_site", {"site_id": None}),)),
            ModelReply("Your site is built. Here is the preview."),
        ]
    )
    site_ids: list[int] = []

    async def fake_complete(*, conversation, **_):
        step = next(script)
        last = conversation.messages[-1]
        if last.get("role") == "tool" and last.get("name") == "create_site":
            site_ids.append(last["content"]["id"])
        if step == "WRITE":
            return ModelReply(
                "",
                (
                    ToolCall(
                        "2",
                        "write_site_files",
                        {
                            "site_id": site_ids[0],
                            "files": [{"path": "src/App.jsx", "content": "broken("}],
                        },
                    ),
                ),
            )
        if step == "FIX":
            assert "src/App.jsx (3:10)" in str(last["content"])
            return ModelReply(
                "",
                (
                    ToolCall(
                        "4",
                        "write_site_files",
                        {
                            "site_id": site_ids[0],
                            "files": [{"path": "src/App.jsx", "content": app_code}],
                        },
                    ),
                ),
            )
        if step.tool_calls and step.tool_calls[0].name == "build_site":
            call = step.tool_calls[0]
            return ModelReply(
                "", (ToolCall(call.id, call.name, {"site_id": site_ids[0]}),)
            )
        return step

    monkeypatch.setattr(studio_session, "complete", fake_complete)
    result = await studio_session.run_turn(
        session=None,
        model=BuilderModel(provider="anthropic", model="m", api_key="k"),
        organization_id=org.id,
        user_id=user.id,
        message="Build a website for Sunrise Dental",
    )

    assert result.reply == "Your site is built. Here is the preview."
    assert result.site_id == site_ids[0]
    assert result.actions == [
        "create_site",
        "write_site_files",
        "build_site",
        "write_site_files",
        "build_site",
    ]
    assert sent[1]["src/App.jsx"] == app_code
    site = await sites.get_site(site_ids[0], organization_id=org.id)
    assert site.build_status == "succeeded"
    # The code is on the site, not in the transcript the browser carries.
    assert app_code not in str(result.conversation)
