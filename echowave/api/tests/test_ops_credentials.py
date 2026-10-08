"""Stream ops, handoff 34: provider key lifecycle.

What these defend: stage -> validate -> activate -> refresh -> verify ->
revoke runs in that order and no other; nothing ever returns key material
(only the last four); a rejected key stops at validate; an unverified key
needs ``force``; activation keeps the old ciphertext so revert puts the old
key back, and revoke makes revert impossible; a second rotation for a slot
in flight is refused; the secret store is written only inside its namespace;
every step is audited; and the HTTP routes are superadmin-only.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from api import constants
from api.db.models import (
    AdminActionLogModel,
    PlatformProviderCredentialModel,
    UserModel,
)
from api.enums import StaffRole
from api.services import features
from api.services.configuration import key_validation, platform_credentials
from api.services.ops import credentials, secret_store

OLD_KEY = "sk-old-AAAAAAAAAAAA1111"
NEW_KEY = "sk-new-BBBBBBBBBBBB2222"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(
        platform_credentials,
        "PLATFORM_CREDENTIAL_SECRET",
        Fernet.generate_key().decode(),
    )
    yield
    features.clear_snapshot()


def _validator(outcome):
    async def validate(provider, key):
        return key_validation.ValidationResult(outcome, f"{outcome} for test")

    return validate


async def _noop():
    return None


class Store:
    backend = "aws_secrets_manager"

    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    async def store(self, component, provider, value):
        if self.fail:
            raise secret_store.SecretStoreError("denied")
        self.writes.append((component, provider, value))
        return secret_store.secret_name(
            component, provider, namespace="decibyl/test/providers/"
        )


async def _admin(session, slug="admin"):
    user = UserModel(
        provider_id=f"ops-cred-{slug}", staff_role=StaffRole.SUPERADMIN.value
    )
    session.add(user)
    await session.flush()
    return user


async def _live(session, key=OLD_KEY):
    await platform_credentials.set_credential(
        session, actor_user_id=None, component="llm", provider="openai", api_key=key
    )
    await session.flush()


def _no_secret(value) -> None:
    flat = json.dumps(value, default=str)
    for secret in (OLD_KEY, NEW_KEY):
        assert secret not in flat
        assert secret[:-4] not in flat


@pytest.mark.asyncio
async def test_the_whole_rotation_and_no_step_returns_a_key(db_session, async_session):
    admin = await _admin(async_session)
    await _live(async_session)
    store = Store()

    staged = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=NEW_KEY,
        reason="quarterly rotation",
    )
    _no_secret(staged.as_dict())
    assert staged.state == credentials.STAGED
    assert staged.staged_key == "••••2222"
    # Nothing uses a staged key: the live one is still the old key.
    live = await platform_credentials.resolve_api_key(
        async_session, component="llm", provider="openai"
    )
    assert live == OLD_KEY

    validated = await credentials.validate(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        validator=_validator("valid"),
    )
    activated = await credentials.activate(
        async_session, rotation_id=staged.id, actor_user_id=admin.id
    )
    assert activated.can_revert and activated.previous_key == "••••1111"
    assert (
        await platform_credentials.resolve_api_key(
            async_session, component="llm", provider="openai"
        )
        == NEW_KEY
    )
    broadcasts = []

    async def broadcast():
        broadcasts.append(1)

    refreshed = await credentials.refresh_consumers(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        store=store,
        broadcast=broadcast,
    )
    assert broadcasts == [1]
    assert store.writes == [("llm", "openai", NEW_KEY)]
    verified = await credentials.verify(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        validator=_validator("valid"),
    )
    revoked = await credentials.revoke(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        reason="old key retired",
    )
    assert revoked.state == credentials.REVOKED
    assert revoked.can_revert is False
    assert "Revoke it on the provider" in revoked.provider_revocation_reminder
    for view in (validated, activated, refreshed, verified, revoked):
        _no_secret(view.as_dict())
    keys = await credentials.provider_keys(async_session)
    _no_secret(keys)
    assert keys[0]["health"] == "valid"
    actions = [
        r.action
        for r in (await async_session.scalars(select(AdminActionLogModel))).all()
        if r.action.startswith("credential_")
    ]
    assert actions == [
        "credential_staged",
        "credential_validated",
        "credential_activated",
        "credential_refreshed",
        "credential_verified",
        "credential_revoked",
    ]
    for row in (await async_session.scalars(select(AdminActionLogModel))).all():
        assert NEW_KEY not in (row.note or "")


@pytest.mark.asyncio
async def test_revert_puts_the_old_key_back(db_session, async_session):
    admin = await _admin(async_session)
    await _live(async_session)
    staged = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=NEW_KEY,
        reason="rotation",
    )
    await credentials.validate(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        validator=_validator("valid"),
    )
    await credentials.activate(
        async_session, rotation_id=staged.id, actor_user_id=admin.id
    )
    with pytest.raises(credentials.LifecycleError):
        await credentials.verify(
            async_session,
            rotation_id=staged.id,
            actor_user_id=admin.id,
            validator=_validator("invalid"),
        )
    reverted = await credentials.revert(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        reason="rejected live",
        broadcast=_noop,
    )
    assert reverted.state == credentials.REVERTED
    assert (
        await platform_credentials.resolve_api_key(
            async_session, component="llm", provider="openai"
        )
        == OLD_KEY
    )


@pytest.mark.asyncio
async def test_a_rejected_key_stops_at_validate(db_session, async_session):
    admin = await _admin(async_session)
    staged = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=NEW_KEY,
        reason="first key",
    )
    failed = await credentials.validate(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        validator=_validator("invalid"),
    )
    assert failed.state == credentials.VALIDATION_FAILED
    assert failed.reason_code in ("unauthorized", "invalid_input", "other")
    with pytest.raises(credentials.StepNotAllowed):
        await credentials.activate(
            async_session, rotation_id=staged.id, actor_user_id=admin.id
        )


@pytest.mark.asyncio
async def test_unverified_needs_force(db_session, async_session):
    admin = await _admin(async_session)
    staged = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=NEW_KEY,
        reason="first key",
    )
    view = await credentials.validate(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        validator=_validator("unverified"),
    )
    assert view.state == credentials.UNVERIFIED
    with pytest.raises(credentials.LifecycleError):
        await credentials.activate(
            async_session, rotation_id=staged.id, actor_user_id=admin.id
        )
    active = await credentials.activate(
        async_session, rotation_id=staged.id, actor_user_id=admin.id, force=True
    )
    assert active.state == credentials.ACTIVE
    row = await async_session.scalar(select(PlatformProviderCredentialModel))
    # Unverified stays unknown on the live row, never "good".
    assert row.last_check_ok is None
    # No previous key: revert takes the slot out of service.
    await credentials.revert(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        reason="went live untested",
        broadcast=_noop,
    )
    await async_session.refresh(row)
    assert row.is_active is False


@pytest.mark.asyncio
async def test_one_rotation_per_slot_and_steps_in_order(db_session, async_session):
    admin = await _admin(async_session)
    staged = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=NEW_KEY,
        reason="rotation",
    )
    with pytest.raises(credentials.LifecycleError):
        await credentials.stage(
            async_session,
            actor_user_id=admin.id,
            component="llm",
            provider="openai",
            api_key=OLD_KEY,
            reason="another",
        )
    for step in (credentials.revoke, credentials.revert):
        with pytest.raises(credentials.StepNotAllowed):
            await step(
                async_session,
                rotation_id=staged.id,
                actor_user_id=admin.id,
                reason="nope",
            )
    with pytest.raises(platform_credentials.PlatformCredentialError):
        await credentials.stage(
            async_session,
            actor_user_id=admin.id,
            component="llm",
            provider="other",
            api_key="sk-••••••••••",
            reason="masked",
        )
    await credentials.cancel(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        reason="changed my mind",
    )
    again = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=OLD_KEY,
        reason="after cancel",
    )
    assert again.state == credentials.STAGED


@pytest.mark.asyncio
async def test_a_store_failure_keeps_the_rotation_active_and_says_so(
    db_session, async_session
):
    admin = await _admin(async_session)
    staged = await credentials.stage(
        async_session,
        actor_user_id=admin.id,
        component="llm",
        provider="openai",
        api_key=NEW_KEY,
        reason="rotation",
    )
    await credentials.validate(
        async_session,
        rotation_id=staged.id,
        actor_user_id=admin.id,
        validator=_validator("valid"),
    )
    await credentials.activate(
        async_session, rotation_id=staged.id, actor_user_id=admin.id
    )
    with pytest.raises(credentials.LifecycleError):
        await credentials.refresh_consumers(
            async_session,
            rotation_id=staged.id,
            actor_user_id=admin.id,
            store=Store(fail=True),
            broadcast=_noop,
        )
    view = await credentials.get(async_session, staged.id)
    assert view.state == credentials.ACTIVE
    assert view.can_revert


def test_secret_names_stay_in_the_namespace():
    assert (
        secret_store.secret_name(
            "llm", "openai", namespace="decibyl/production/providers/"
        )
        == "decibyl/production/providers/llm/openai"
    )
    for component, provider in (
        ("llm", "../../root"),
        ("llm", "a/b"),
        ("", "x"),
        ("llm", "A"),
    ):
        with pytest.raises(secret_store.SecretStoreError):
            secret_store.secret_name(
                component, provider, namespace="decibyl/production/providers/"
            )
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.secret_name("llm", "openai", namespace="no-trailing-slash")


@pytest.mark.asyncio
async def test_aws_store_writes_then_creates(monkeypatch):
    calls = []

    class NotFound(Exception):
        response = {"Error": {"Code": "ResourceNotFoundException"}}

    class Client:
        def put_secret_value(self, **kwargs):
            calls.append(("put", kwargs["SecretId"]))
            raise NotFound()

        def create_secret(self, **kwargs):
            calls.append(("create", kwargs["Name"]))

    store = secret_store.AwsSecretsManagerStore(
        Client(), namespace="decibyl/test/providers/"
    )
    name = await store.store("llm", "openai", NEW_KEY)
    assert name == "decibyl/test/providers/llm/openai"
    assert calls == [("put", name), ("create", name)]


@pytest.fixture
def as_user(monkeypatch, test_client_factory):
    def _make(user):
        async def _fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user)
        return test_client_factory(user)

    return _make


@pytest.mark.asyncio
async def test_routes_are_superadmin_only_and_never_echo_the_key(
    db_session, async_session, as_user, monkeypatch
):
    monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", True)
    support = UserModel(
        provider_id="ops-cred-support", staff_role=StaffRole.SUPPORT.value
    )
    async_session.add(support)
    admin = await _admin(async_session, "route-admin")
    body = {
        "component": "llm",
        "provider": "openai",
        "api_key": NEW_KEY,
        "reason": "rotation",
    }
    async with as_user(support) as client:
        assert (
            await client.post("/api/v1/admin/ops/credentials/rotations", json=body)
        ).status_code == 403
        assert (await client.get("/api/v1/admin/ops/credentials")).status_code == 403
    async with as_user(admin) as client:
        staged = await client.post("/api/v1/admin/ops/credentials/rotations", json=body)
        assert staged.status_code == 200
        assert NEW_KEY not in staged.text
        rotation_id = staged.json()["id"]
        out_of_order = await client.post(
            f"/api/v1/admin/ops/credentials/rotations/{rotation_id}/revoke",
            json={"reason": "x" * 5},
        )
        assert out_of_order.status_code == 409
        listed = await client.get("/api/v1/admin/ops/credentials")
        assert listed.status_code == 200
        assert NEW_KEY not in listed.text
        unknown = await client.post(
            f"/api/v1/admin/ops/credentials/rotations/{rotation_id}/explode"
        )
        assert unknown.status_code == 404
