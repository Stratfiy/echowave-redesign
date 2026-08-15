"""Prove the Decibyl-managed model tier works, before a customer's call does.

Selling the managed tier means a customer runs an agent on *your* provider
keys, through the Model Proxy Service, instead of bringing their own. That is
one setting away from not selling it, and the two states are almost
indistinguishable from the outside — which is the problem this script exists
for. The failure lands mid-call, on a customer who chose the managed tier
precisely so they would not have to think about model plumbing.

Four things fail only on a real deployment:

    1. **MPS_API_URL was never chosen.** Unset, it inherits
       https://services.decibyl.ai — right for the managed product and wrong
       for any install that is not it. Nothing announces the difference.
    2. **DEPLOYMENT_MODE and the secret disagree.** In ``oss`` mode the client
       sends ``X-Created-By`` and *never sends the secret key at all*. Setting
       DECIBYL_MPS_SECRET_KEY without moving off ``oss`` is a credential that
       looks configured and is never transmitted.
    3. **The host is not reachable from the box**, whatever it answers from a
       laptop — a security group, a private subnet, egress filtering.
    4. **A service key does not authenticate.** Every managed call carries one,
       and the failure is a 401 in a log nobody is reading while a caller waits.

What it does not do: stand MPS up. MPS is a separate service and is not in
this repository. This checks that a deployment configured to use one actually
can.

    # On the deployed box, which is the only place the answers mean anything:
    docker compose exec api python -m scripts.verify_managed_model_tier

    # With a service key, which is the half that proves authentication:
    docker compose exec api python -m scripts.verify_managed_model_tier \
        --service-key dk_live_...

Exit status is 0 when everything checked here passed, 1 otherwise, so it can
sit in a deploy gate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from api.constants import (
    DECIBYL_MPS_SECRET_KEY,
    DEFAULT_MPS_API_URL,
    DEPLOYMENT_MODE,
    MPS_API_URL,
    MPS_API_URL_IS_DEFAULT,
)

PASS = "  ok  "
FAIL = " FAIL "
WARN = " warn "
TODO = " you  "

TIMEOUT = httpx.Timeout(10.0)

_results: list[tuple[str, str, str]] = []


def record(mark: str, title: str, detail: str = "") -> None:
    _results.append((mark, title, detail))
    print(f"[{mark}] {title}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


def check_the_url_was_chosen() -> None:
    """An inherited default is a dependency nobody decided to take."""
    if MPS_API_URL_IS_DEFAULT:
        record(
            WARN,
            f"MPS_API_URL is unset — inheriting {DEFAULT_MPS_API_URL}",
            "Correct for the managed product and wrong for anything else. If\n"
            "you are selling the managed tier, set it explicitly so the\n"
            "dependency is recorded; if you are not, the rest of this script\n"
            "is measuring a host you have no business calling.",
        )
    else:
        record(PASS, f"MPS_API_URL is set explicitly: {MPS_API_URL}")


def check_the_secret_would_be_sent() -> None:
    """The misconfiguration that looks exactly like a working one.

    ``MPSServiceKeyClient._get_headers`` sends ``X-Secret-Key`` only when
    ``DEPLOYMENT_MODE != "oss"``. In oss mode it sends ``X-Created-By`` and the
    secret is never transmitted — so a deployment can hold a correct secret,
    log no error, and authenticate as nobody.
    """
    is_oss = DEPLOYMENT_MODE == "oss"
    has_secret = bool(DECIBYL_MPS_SECRET_KEY)

    if is_oss and has_secret:
        record(
            FAIL,
            "DECIBYL_MPS_SECRET_KEY is set but will never be sent",
            "DEPLOYMENT_MODE is 'oss', and in oss mode the client sends\n"
            "X-Created-By instead of X-Secret-Key. The secret is configured\n"
            "and ignored. Either set DEPLOYMENT_MODE to something other than\n"
            "'oss', or drop the secret so the configuration says what it does.",
        )
    elif not is_oss and not has_secret:
        record(
            FAIL,
            f"DEPLOYMENT_MODE is '{DEPLOYMENT_MODE}' but no secret is set",
            "Outside oss mode the client authenticates to MPS with\n"
            "X-Secret-Key. Without DECIBYL_MPS_SECRET_KEY every service-key\n"
            "call goes out unauthenticated.",
        )
    elif is_oss:
        record(
            PASS,
            "oss mode — service keys are scoped by X-Created-By",
            "No platform secret is involved, which is consistent.",
        )
    else:
        record(PASS, f"DEPLOYMENT_MODE '{DEPLOYMENT_MODE}' with a secret set")


def check_the_scheme() -> None:
    """Realtime speech runs over a websocket derived from this URL.

    ``service_factory`` swaps the scheme rather than reading a second setting,
    so an http:// value silently produces ws:// — plaintext audio for a managed
    STT session, and a mixed-content refusal from any browser page on https.
    """
    parsed = urlparse(MPS_API_URL)
    websocket = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")

    if parsed.scheme == "https":
        record(PASS, f"Realtime speech will connect to {websocket}")
    elif parsed.scheme == "http":
        record(
            WARN,
            f"http:// URL — realtime speech will use {websocket}",
            "Call audio would cross that hop in plaintext. Acceptable only if\n"
            "MPS is on a private network you trust end to end.",
        )
    else:
        record(FAIL, f"MPS_API_URL has no usable scheme: {MPS_API_URL!r}")


async def check_reachable() -> bool:
    """Reachable *from this box*, which is the only question that matters."""
    url = f"{MPS_API_URL}/api/v1/service-keys/usage/self"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url)
    except httpx.ProxyError as exc:
        # Distinguished from a plain connect failure because the remedy is
        # somewhere else entirely: MPS may be perfectly healthy and the egress
        # proxy simply does not allow this host. Diagnosing that as "MPS is
        # down" sends somebody to the wrong team.
        record(
            FAIL,
            "An egress proxy refused the connection to MPS",
            f"{url}\n{exc}\n"
            "The host was never reached. Allow it through the proxy — check\n"
            "HTTPS_PROXY and NO_PROXY in this container's environment — and\n"
            "run this again before concluding anything about MPS itself.",
        )
        return False
    except httpx.ConnectError as exc:
        record(
            FAIL,
            "Cannot reach MPS from this host",
            f"{url}\n{exc}\n"
            "A security group, a private subnet or egress filtering. It does\n"
            "not matter that it answers from a laptop.",
        )
        return False
    except httpx.TimeoutException:
        record(FAIL, f"MPS did not answer within 10s: {url}")
        return False
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
        record(FAIL, f"MPS probe failed: {exc}")
        return False

    # 401/403 is the *good* answer to an unauthenticated request: the request
    # crossed DNS, TLS and whatever sits in front, and was turned away by the
    # thing that is supposed to turn it away.
    if response.status_code in (401, 403):
        record(
            PASS,
            f"MPS is reachable and rejects unauthenticated calls ({response.status_code})",
        )
        return True
    if response.status_code == 200:
        record(
            FAIL,
            "MPS answered an unauthenticated request with 200",
            "Worse than unreachable: something is serving managed model usage\n"
            "to anyone who asks. Do not put customer traffic on this.",
        )
        return True
    if response.status_code == 404:
        record(
            FAIL,
            f"MPS answered 404 for {url}",
            "Reachable, but not serving the service-key API this platform\n"
            "expects. Check that MPS_API_URL points at the service itself and\n"
            "not at a proxy in front of something else.",
        )
        return True

    record(
        WARN,
        f"MPS answered {response.status_code} to an unauthenticated probe",
        "Expected 401 or 403. Reachable, but not behaving as expected.",
    )
    return True


async def check_a_service_key(service_key: str) -> None:
    """The half a customer's call actually depends on."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        headers = {
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }

        try:
            usage = await client.get(
                f"{MPS_API_URL}/api/v1/service-keys/usage/self", headers=headers
            )
        except Exception as exc:  # noqa: BLE001 — see above
            record(FAIL, f"Service key validation failed: {exc}")
            return

        if usage.status_code == 200:
            record(
                PASS,
                "Service key authenticates against MPS",
                "This is the call validate_decibyl_service_key makes, so the\n"
                "`decibyl` provider will accept this key in Model Configurations.",
            )
        else:
            record(
                FAIL,
                f"Service key rejected: {usage.status_code}",
                "Every managed call carries this key. The failure would land\n"
                "mid-call as a 401 in a log nobody is reading.",
            )
            return

        try:
            correlation = await client.post(
                f"{MPS_API_URL}/api/v1/service-keys/correlation-id/self",
                json={},
                headers=headers,
            )
        except Exception as exc:  # noqa: BLE001 — see above
            record(WARN, f"Could not mint a correlation id: {exc}")
            return

        if correlation.status_code == 200 and correlation.json().get("correlation_id"):
            record(
                PASS,
                "MPS mints correlation ids",
                "What ties a managed model call back to the run that made it —\n"
                "the difference between a usage bill you can explain and one\n"
                "you cannot.",
            )
        else:
            record(
                WARN,
                f"Correlation id request answered {correlation.status_code}",
                "Managed calls still work; they are sent un-correlated, so\n"
                "per-run attribution of managed usage is lost.",
            )


async def check_the_llm_surface() -> None:
    """The OpenAI-compatible base every managed model call is built on.

    ``{MPS_API_URL}/api/v1/llm`` is handed to AsyncOpenAI, which appends
    ``/chat/completions`` and ``/embeddings``. A base URL that 404s here is a
    managed tier that cannot answer a single question.
    """
    url = f"{MPS_API_URL}/api/v1/llm/models"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 — see above
        record(WARN, f"Could not probe the managed LLM surface: {exc}")
        return

    if response.status_code in (200, 401, 403):
        record(
            PASS,
            f"Managed LLM base is serving ({response.status_code})",
            f"{MPS_API_URL}/api/v1/llm — where chat completions and embeddings\n"
            "are proxied from.",
        )
    else:
        record(
            WARN,
            f"Managed LLM base answered {response.status_code}",
            "Not conclusive — /models is optional — but worth confirming that\n"
            f"{MPS_API_URL}/api/v1/llm serves an OpenAI-compatible API.",
        )


async def run(service_key: str | None) -> None:
    check_the_url_was_chosen()
    check_the_secret_would_be_sent()
    check_the_scheme()

    if not await check_reachable():
        record(
            TODO,
            "Remaining checks skipped — MPS is not reachable",
            "Fix connectivity first; everything below depends on it.",
        )
        return

    await check_the_llm_surface()

    if service_key:
        await check_a_service_key(service_key)
    else:
        record(
            TODO,
            "Run again with --service-key to prove authentication",
            "Issue one from Settings > Service Keys, or with an existing key\n"
            "from an account already on the managed tier. Without it, this\n"
            "script has shown the service is reachable and not that a customer\n"
            "can use it.",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Decibyl-managed model tier on this deployment."
    )
    parser.add_argument(
        "--service-key",
        help="A Decibyl service key, to prove authentication end to end.",
    )
    args = parser.parse_args()

    asyncio.run(run(args.service_key))

    print()
    failures = [row for row in _results if row[0] == FAIL]
    outstanding = [row for row in _results if row[0] == TODO]

    if failures:
        print(f"{len(failures)} check(s) failed.")
        return 1
    if outstanding:
        print("Everything checked here passed. What is left is listed above.")
        return 0
    print("Every check passed. The managed model tier works on this deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
