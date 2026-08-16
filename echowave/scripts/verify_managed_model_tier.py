"""Can a customer actually pick "Decibyl" and have their agent work?

The managed tier means a customer runs on *your* provider keys instead of
bringing their own. That is a promise made on a pricing page and kept by three
things lining up on this box, none of which announce themselves when they do
not:

    1. **PLATFORM_CREDENTIAL_SECRET is set.** Platform keys are encrypted at
       rest with it and there is deliberately no default, so without it the
       admin screen cannot store a key at all.
    2. **A key is stored for every vendor a tier resolves to.** The tiers are a
       menu — "fast", "accurate" — and each one maps to a real vendor. A
       customer picking a tier whose vendor has no key gets a call that fails
       in that vendor's own branch with that vendor's own error.
    3. **The key still works.** Keys expire, get rotated at the vendor, and run
       out of quota. Nothing here notices until a call does.

The failure this exists for is specific and quiet: ``managed_resolution.apply``
logs an error and **continues** when a platform key is missing, leaving the
section as ``provider=decibyl``. The call then falls through to a legacy path
and fails somewhere that reads nothing like "you never added a Google key".

    # On the deployed box, which is the only place the answers mean anything:
    docker compose exec api python -m scripts.verify_managed_model_tier

Exit status is 0 when every tier a customer can pick is servable, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.db import db_client
from api.enums import CostComponent
from api.services.configuration import managed_tiers, platform_credentials

PASS = "  ok  "
FAIL = " FAIL "
WARN = " warn "
TODO = " you  "

_results: list[tuple[str, str, str]] = []


def record(mark: str, title: str, detail: str = "") -> None:
    _results.append((mark, title, detail))
    print(f"[{mark}] {title}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


#: Every (component, tier) a customer can choose, in the order the model screen
#: offers them. Read from managed_tiers rather than restated, so a tier added
#: there is checked here without anyone remembering to.
def _every_tier() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for tier in managed_tiers.LLM_TIERS:
        pairs.append(("llm", tier))
    for tier in managed_tiers.STT_TIERS:
        pairs.append(("stt", tier))
    for tier in managed_tiers.TTS_TIERS:
        pairs.append(("tts", tier))
    for tier in managed_tiers.REALTIME_TIERS:
        pairs.append((managed_tiers.REALTIME_COMPONENT, tier))
    for tier in managed_tiers.EMBEDDINGS_TIERS:
        pairs.append((managed_tiers.EMBEDDINGS_COMPONENT, tier))
    return pairs


def _credential_component(component: str) -> CostComponent:
    """Which stored key authenticates this component.

    Mirrors ``managed_resolution._credential_component``: embeddings and
    speech-to-speech both authenticate on the LLM credential, because a vendor
    issues one key that serves chat, embeddings and realtime alike.
    """
    if component in (
        managed_tiers.EMBEDDINGS_COMPONENT,
        managed_tiers.REALTIME_COMPONENT,
    ):
        return CostComponent.LLM
    return CostComponent(component)


def check_encryption() -> bool:
    if platform_credentials.encryption_is_configured():
        record(PASS, "PLATFORM_CREDENTIAL_SECRET is set — keys can be stored")
        return True

    record(
        FAIL,
        "PLATFORM_CREDENTIAL_SECRET is not set",
        "Platform keys are encrypted at rest with it and there is no default,\n"
        "so the admin screen cannot store one. Generate a key with:\n"
        '  python -c "from cryptography.fernet import Fernet; '
        'print(Fernet.generate_key().decode())"\n'
        "add it to .env by hand — the deploy never rewrites .env — and\n"
        "restart the API.",
    )
    return False


async def check_the_tiers() -> None:
    """Every tier a customer can pick, and whether a key exists to serve it."""
    async with db_client.async_session() as session:
        stored = await platform_credentials.list_credentials(session)
        active = {
            (credential.component, credential.provider.lower())
            for credential in stored
            if credential.is_active
        }
        inactive = {
            (credential.component, credential.provider.lower())
            for credential in stored
            if not credential.is_active
        }

    if not stored:
        record(
            FAIL,
            "No platform provider keys are stored at all",
            "Every managed tier below is unservable. Add keys at\n"
            "/admin/provider-keys (superuser only).",
        )

    servable: list[str] = []
    missing: dict[str, list[str]] = {}

    for component, tier in _every_tier():
        upstream = managed_tiers.resolve(component, tier)
        needed = _credential_component(component)
        vendor = upstream.provider.lower()
        key = (needed.value, vendor)

        # Speech-to-speech authenticates on the vendor's ordinary key, so a
        # stored 'openai' serves 'openai_realtime'. Mirrors the fallback in
        # platform_credentials.resolve_api_key — only the suffix is stripped,
        # never a general "before the underscore", because azure_speech is a
        # different account from azure.
        if key not in active and vendor.endswith(platform_credentials.REALTIME_SUFFIX):
            base = vendor[: -len(platform_credentials.REALTIME_SUFFIX)]
            if (needed.value, base) in active:
                key = (needed.value, base)

        label = f"{component}/{tier} → {upstream.provider}:{upstream.model}"

        if key in active:
            servable.append(label)
        elif key in inactive:
            record(
                FAIL,
                f"{label} — key stored but marked inactive",
                "A customer picking this tier gets a call that fails in the\n"
                f"{upstream.provider} branch. Re-activate it or remove the tier.",
            )
        else:
            missing.setdefault(upstream.provider, []).append(label)

    if servable:
        record(
            PASS,
            f"{len(servable)} managed tier(s) are servable",
            "\n".join(servable),
        )

    for provider, labels in sorted(missing.items()):
        detail = (
            "\n".join(labels)
            + "\n"
            + "managed_resolution logs an error and continues, leaving the\n"
            "section as provider=decibyl, so the call fails somewhere that\n"
            "reads nothing like 'you never added this key'."
        )

        record(
            FAIL,
            f"No active platform key for '{provider}' — {len(labels)} tier(s) unservable",
            detail,
        )

    if servable and not missing:
        record(
            TODO,
            "Keys exist, but nothing here proves they still work",
            "Keys expire, get rotated at the vendor, and run out of quota.\n"
            "Place one test call on a managed configuration to close this.",
        )


async def run() -> None:
    if not check_encryption():
        record(
            TODO,
            "Tier checks skipped — no key can be stored without the secret",
        )
        return
    await check_the_tiers()


def main() -> int:
    argparse.ArgumentParser(
        description="Verify the Decibyl-managed model tier on this deployment."
    ).parse_args()

    asyncio.run(run())

    print()
    failures = [row for row in _results if row[0] == FAIL]
    outstanding = [row for row in _results if row[0] == TODO]

    if failures:
        print(
            f"{len(failures)} check(s) failed — the managed tier is not sellable yet."
        )
        return 1
    if outstanding:
        print("Everything checkable from here passed. What is left is listed above.")
        return 0
    print("Every managed tier a customer can pick has a key behind it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
