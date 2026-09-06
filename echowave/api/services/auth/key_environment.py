"""Whether an API key is allowed to ring a stranger.

One key does everything today. Handing a developer, an agency or a contractor
access to the API means handing them the ability to dial your customers — and
there is no way to give somebody a key that can build against the product
without that. Every platform that solved this solved it the same way: a key
carries an environment, and the sandbox one cannot reach the real world.

**Sandbox is defined by who it may dial, not by which route it may call.**
Blocking the published-agent endpoint would have been easier and would have
meant nothing: the draft endpoint dials a real phone just as hard. So a
sandbox key may only place calls to numbers the organization has already
proved it can answer — the same ``verified_numbers`` gate the test-call button
uses, for the same reason, and one that has been in production long enough to
trust.

**It is not free, and nothing here pretends otherwise.** A sandbox call runs
real speech recognition, a real model and a real carrier, and costs what it
costs. What the environment buys is that the cost is attributable: every run
records which environment started it, so a month's usage can be split into
what customers generated and what a developer did while testing.

Existing keys are production. Not a default chosen for convenience — it is the
only correct answer. Every key that exists today was issued to do real work,
and a migration that quietly demoted them would take an account's integration
down at the moment it deployed.
"""

from __future__ import annotations

from typing import Any

PRODUCTION = "production"
SANDBOX = "sandbox"

ENVIRONMENTS: tuple[str, ...] = (PRODUCTION, SANDBOX)

#: What a key looks like, per environment. The prefix is the point: a key
#: pasted into a config file, a log line or a support ticket says which world
#: it belongs to without anybody having to look it up.
_PREFIXES = {PRODUCTION: "dcb_", SANDBOX: "dcb_test_"}


def normalise(raw: Any) -> str:
    """The environment for a key, defaulting to production.

    ``None`` is what every key issued before this feature has stored, and it
    means production — see the module docstring. An unrecognised value means
    the same, because the alternative is a key that silently stops working on
    a string nobody can see.
    """
    if isinstance(raw, str) and raw.strip().lower() in ENVIRONMENTS:
        return raw.strip().lower()
    return PRODUCTION


def prefix_for(environment: Any) -> str:
    return _PREFIXES[normalise(environment)]


def is_sandbox(environment: Any) -> bool:
    return normalise(environment) == SANDBOX


def environment_of_key(raw_key: Any) -> str:
    """Read the environment off the key itself.

    A convenience for a caller holding only the string — the stored column is
    the authority, and this must never be used to grant anything. It is read
    the other way round on purpose: only the explicit sandbox prefix produces
    sandbox, so a malformed or truncated key reads as production and gets the
    *stricter* treatment everywhere this is used to warn rather than to allow.
    """
    if isinstance(raw_key, str) and raw_key.startswith(_PREFIXES[SANDBOX]):
        return SANDBOX
    return PRODUCTION


def refusal_message(destination: str) -> str:
    """What a sandbox key is told when it aims at an unverified number."""
    return (
        f"This is a sandbox key, so it can only call numbers your organization "
        f"has verified. Add {destination} under Verified numbers, or use a "
        f"production key."
    )
