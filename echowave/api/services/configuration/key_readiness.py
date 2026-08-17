"""Whether this agent has the keys it needs, asked in one place.

A slot set to the account's own key with no usable key behind it used to
*connect* — the section kept its vendor and an empty string, the pipeline built
a service that could not authenticate, and the caller listened to silence for
the length of the call while we paid the carrier for the minutes. Nothing said
what had happened, to the caller or to the operator.

There are two honest answers to that, and the account chooses between them with
``OrganizationPreferences.byok_fallback_to_managed``:

* **off (default)** — refuse the call. Nobody is billed for a choice they did
  not make, and the refusal names the slot that needs a key.
* **on** — run on Decibyl's key for the same vendor and model, billed at the
  published rate. Handled in ``byok_resolution.apply``; by the time this gate
  runs there is nothing left unresolved for it to refuse.

This module is the *first* answer. It lives here, next to the resolution it
mirrors, and is asked by every path that dials — the same arrangement, and for
the same reason, as ``services/workflow/liveness``: a gate honoured by three
callers out of four is worse than no gate, because the operator believes the
call will be refused and it goes out anyway.
"""

from __future__ import annotations

from loguru import logger

from api.services.configuration import byok_resolution

#: What each section is called on the screen the customer would go to. The
#: internal names ("stt", "tts") are not what the provider-keys page says, and a
#: refusal that names a field nobody can find is a refusal nobody can act on.
_SECTION_LABELS: dict[str, str] = {
    "stt": "transcriber",
    "llm": "language model",
    "tts": "voice",
    "realtime": "realtime model",
    "embeddings": "embeddings",
}


class ProviderKeyMissing(Exception):
    """A slot runs on the account's own key and no usable key is stored.

    A refusal the caller is expected to surface, not an error to retry — the
    same shape as ``dnd.CallRefused`` and ``liveness.AgentNotTakingCalls``.
    Retrying changes nothing until somebody adds a key, switches the slot to
    managed, or opts into falling back.
    """

    #: Short, stable token stored against a refused campaign row and returned to
    #: the API caller. The human sentence changes; this does not.
    reason: str = "provider_key_missing"

    def __init__(self, message: str, *, sections: list[str] | None = None) -> None:
        super().__init__(message)
        self.sections = sections or []


def _sections_in_use(effective) -> set[str]:
    """The slots this call will actually run through.

    ``byok_resolution`` reports on every BYOK section it knows about, which
    includes ``embeddings`` and — on a pipeline agent — ``realtime``. Neither is
    touched by a voice call, so refusing on them refuses a call over a slot it
    would never have used. That is not a hypothetical: it took inbound calling
    down, on accounts whose pipeline was configured correctly.
    """
    if getattr(effective, "is_realtime", False):
        return {"realtime", "llm"}
    return {"stt", "llm", "tts"}


def _describe(sections: list[str]) -> str:
    labels = [_SECTION_LABELS.get(name, name) for name in sections]
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


async def assert_keys_available(
    effective,
    *,
    organization_id: int | None,
    allow_managed_fallback: bool,
) -> None:
    """Raise :class:`ProviderKeyMissing` if a slot cannot be run.

    Takes the already-resolved configuration rather than fetching its own: the
    caller has resolved it to dial with, and resolving a second time here would
    be a second chance to get a different answer than the one about to be used.

    A no-op when the account has opted into falling back, because
    ``byok_resolution.apply`` will have moved those sections onto the platform
    key already — there is nothing left for this to refuse.
    """
    if organization_id is None or allow_managed_fallback:
        return

    missing = [
        name
        for name in await byok_resolution.missing_keys(
            effective, organization_id=organization_id
        )
        if name in _sections_in_use(effective)
    ]
    if not missing:
        return

    logger.warning(
        "Refusing a call for organization {}: no usable key for {}.",
        organization_id,
        ", ".join(missing),
    )
    raise ProviderKeyMissing(
        f"This agent's {_describe(missing)} is set to your own key, and no "
        "usable key is stored for it. Add one under Provider keys, switch the "
        "slot to Decibyl's models, or turn on falling back to Decibyl's keys.",
        sections=missing,
    )


async def assert_workflow_may_run(workflow) -> None:
    """The pre-dial form of :func:`assert_keys_available`.

    Resolves what this agent would run with and refuses if a slot cannot be
    filled. Takes the workflow because that is what a dial path has in hand
    before a run exists — the run's own definition is only available once the
    call is under way, by which point refusing is no longer free.

    Tolerant of a workflow object without an organization: those are the paths
    that cannot consult a vault anyway, and refusing them here would break
    callers this gate is not about.
    """
    organization_id = getattr(workflow, "organization_id", None)
    if organization_id is None:
        return

    from api.services.configuration.ai_model_configuration import (
        _managed_fallback_allowed,
        get_effective_ai_model_configuration_for_workflow,
    )

    allow_managed_fallback = await _managed_fallback_allowed(organization_id)
    if allow_managed_fallback:
        # Nothing to refuse: resolution will move the unfilled slots onto the
        # platform key. Returning before resolving keeps this gate free for the
        # accounts that have opted in.
        return

    effective = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=getattr(workflow, "workflow_configurations", None),
    )
    await assert_keys_available(
        effective,
        organization_id=organization_id,
        allow_managed_fallback=False,
    )
