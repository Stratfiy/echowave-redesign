"""Load platform provider keys from the environment at boot.

The keys that make an account "managed" are held encrypted in the database and
entered through the staff screen. That is the right home for them, but it makes
a fresh deployment a manual errand: bring the box up, log in as staff, paste
three keys, and until someone does, every managed signup is broken.

This seeds the same vault from environment variables instead, so a box that
comes up has a working managed tier without anyone clicking anything:

    PLATFORM_KEY_LLM_GOOGLE=...
    PLATFORM_KEY_STT_SARVAM=...
    PLATFORM_KEY_TTS_SARVAM=...

The variable name carries the component and the provider, which is what keeps
this general — adding a provider is a new line in the environment file, not a
change here.

**The environment is a seed, not the source of truth.** A key set here is
written into the encrypted vault and then behaves exactly like one typed into
the staff screen: rotatable, deactivatable, maskable. Re-seeding an unchanged
key is a no-op, so a restart does not churn the table or the audit log.

Keys belong in the deployment's own environment file, which is not in version
control, and never in source. A key committed to a repository is a key that has
to be rotated at the provider the moment anyone notices, and the repository
history keeps it long after the file stops showing it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from api.db import db_client
from api.enums import CostComponent
from api.services.configuration import platform_credentials

#: Only this prefix is read, so unrelated environment variables cannot be
#: mistaken for credentials.
ENV_PREFIX = "PLATFORM_KEY_"

#: How the seed identifies itself in the vault, so an operator looking at the
#: staff screen can tell which keys arrived from the environment.
SEED_LABEL = "Seeded from environment"


@dataclass(frozen=True)
class SeedResult:
    """What a seeding pass did, for logs and tests. Never carries a key."""

    stored: list[tuple[str, str]]
    unchanged: list[tuple[str, str]]
    skipped: list[tuple[str, str]]

    @property
    def total(self) -> int:
        return len(self.stored) + len(self.unchanged) + len(self.skipped)


def _parse(name: str) -> tuple[str, str] | None:
    """``PLATFORM_KEY_LLM_OPENAI_REALTIME`` → ``("llm", "openai_realtime")``.

    Split on the first underscore only: component names never contain one and
    provider names frequently do, so anything after the first separator belongs
    to the provider.
    """
    remainder = name[len(ENV_PREFIX) :]
    if "_" not in remainder:
        return None
    component, provider = remainder.split("_", 1)
    component = component.strip().lower()
    provider = provider.strip().lower()
    if not component or not provider:
        return None
    return component, provider


def _declared() -> list[tuple[str, str, str]]:
    """Every ``(component, provider, key)`` the environment declares."""
    found: list[tuple[str, str, str]] = []
    for name, value in sorted(os.environ.items()):
        if not name.startswith(ENV_PREFIX):
            continue
        parsed = _parse(name)
        if parsed is None:
            logger.warning(
                "Ignoring {}: the name must be {}<COMPONENT>_<PROVIDER>, "
                "for example {}LLM_GOOGLE.",
                name,
                ENV_PREFIX,
                ENV_PREFIX,
            )
            continue
        key = (value or "").strip()
        if not key:
            # An empty variable is how a deployment says "not this one yet",
            # which is ordinary rather than a misconfiguration.
            continue
        component, provider = parsed
        found.append((component, provider, key))
    return found


async def seed_from_environment() -> SeedResult:
    """Write every key the environment declares into the platform vault.

    Never raises. A malformed variable, an unknown component or an unset
    encryption secret is logged and skipped — none of them is a reason to stop
    an API process from starting, and a box that boots with a broken managed
    tier is far easier to diagnose than one that does not boot.
    """
    declared = _declared()
    if not declared:
        return SeedResult(stored=[], unchanged=[], skipped=[])

    if not platform_credentials.encryption_is_configured():
        logger.error(
            "{} variables are set but PLATFORM_CREDENTIAL_SECRET is not, so "
            "they cannot be encrypted and the managed tier will have no keys. "
            'Generate one with: python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())"',
            ENV_PREFIX,
        )
        return SeedResult(
            stored=[], unchanged=[], skipped=[(c, p) for c, p, _ in declared]
        )

    stored: list[tuple[str, str]] = []
    unchanged: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    async with db_client.async_session() as session:
        for component, provider, key in declared:
            try:
                current = await platform_credentials.resolve_api_key(
                    session, component=component, provider=provider
                )
            except Exception:
                current = None

            if current == key:
                # Already installed and identical. Writing it again would
                # rewrite the row and reset set_by on every restart.
                unchanged.append((component, provider))
                continue

            try:
                await platform_credentials.set_credential(
                    session,
                    actor_user_id=None,
                    component=CostComponent(component),
                    provider=provider,
                    api_key=key,
                    label=SEED_LABEL,
                )
                stored.append((component, provider))
            except (platform_credentials.PlatformCredentialError, ValueError) as e:
                # The message names the component and provider, never the value.
                logger.error(
                    "Could not seed the platform {} key for {}: {}",
                    component,
                    provider,
                    e,
                )
                skipped.append((component, provider))

        await session.commit()

    if stored:
        logger.info(
            "Seeded {} platform key(s) from the environment: {}",
            len(stored),
            ", ".join(f"{c}/{p}" for c, p in stored),
        )
    if unchanged:
        logger.debug(
            "{} platform key(s) already matched the environment", len(unchanged)
        )
    if skipped:
        logger.warning(
            "{} platform key(s) from the environment could not be stored: {}",
            len(skipped),
            ", ".join(f"{c}/{p}" for c, p in skipped),
        )

    return SeedResult(stored=stored, unchanged=unchanged, skipped=skipped)
