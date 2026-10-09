"""The supervisor by phone, through a Plivo Multi-Party Call (MPC).

    ┌──────────────────────────────────────────────────────────────────────┐
    │ UNCONFIRMED -- every Plivo assumption this feature makes is here and   │
    │ nowhere else. Questions are open with Plivo; until they are answered  │
    │ this bridge is off (``LIVE_TAKEOVER_BRIDGE`` defaults to "pipeline")   │
    │ and the tests exercise it only against a recorded fake.               │
    └──────────────────────────────────────────────────────────────────────┘

ASSUMPTIONS (each one is a question for Plivo or for a test call):

A1. **The call is already in an MPC.** Today a Decibyl call is a two-party
    call with a bidirectional ``<Stream>`` to Pipecat. Moving it into an MPC
    mid-call (Transfer API -> ``<MultiPartyCall role="Customer">``) ends the
    stream, and with it the pipeline, so this bridge does **not** do that.
    It serves only calls that were started inside an MPC with the agent as
    a participant, and reads the MPC from the run's ``gathered_context``:
    ``plivo_mpc_name`` and ``plivo_mpc_agent_member_id``. Nothing in
    Decibyl sets those yet; a call without them is refused
    (``BridgeUnavailable``) and the person joining is told so.
A2. **Endpoints** (``https://api.plivo.com/v1/Account/{auth_id}``):
    add a participant ``POST /MultiPartyCall/name_{name}/``; update one
    ``POST /MultiPartyCall/name_{name}/Participant/{member_id}/``; remove one
    ``DELETE /MultiPartyCall/name_{name}/Participant/{member_id}/``; list
    them ``GET /MultiPartyCall/name_{name}/Participant/``. JSON bodies, HTTP
    basic auth with the account's auth id and token.
A3. **Adding the supervisor** with ``role="Supervisor"``, ``from`` the
    number the call was made from or to, ``to`` the supervisor's phone, and
    ``coach_mode`` dials them and returns a ``request_uuid`` and the new
    leg's ``call_uuid`` (``calls[0].call_uuid``) -- not the member id.
A4. **The member id** of a participant is found by listing participants and
    matching ``call_uuid``; the listing's objects carry ``member_id`` and
    ``call_uuid``. It may take a moment to appear after the dial is answered,
    so it is looked up when first needed, not at join.
A5. **coach_mode** on a Supervisor: ``true`` -- heard by agents only, not
    the caller; ``false`` -- heard by everyone (Plivo's guide calls this
    take-over; its own code comments contradict each other on this point).
    So both barge and take-over use ``coach_mode=false``; "coach" is
    supported by the bridge for a supervisor who should only be heard by
    the agent leg, which Decibyl's own whisper does better today (open
    question: does coach audio reach an ``ai-agent`` participant at all?).
A6. **Muting the agent** with ``mute=true`` on its participant silences
    its leg toward the caller and the supervisor, and ``mute=false`` undoes
    it. Muting is done on top of the pipeline gates, never instead of them:
    a muted leg that still generated replies would answer nobody and spend
    money doing it.
A7. **Removing the supervisor** (DELETE on their member id) hangs up their
    leg and leaves the MPC running for the caller and the agent.
A8. **Beeps**: MPC's default join/leave/state-change beeps are left on. A
    beep when somebody joins a customer's call is the honest default; the
    choice is the business's, not this module's.
A9. **Presence**: nothing here listens to MPC participant callbacks. A
    supervisor on the phone is "still there" while their listen panel's
    talking socket is open (it opens with no microphone), so closing the
    page hands the call back after ``LIVE_TAKEOVER_RECOVERY_SECONDS``; but
    hanging up the phone with the page open does not, until somebody presses
    Hand back. Driving recovery from the participant-exit callback is the
    fix once the callback's shape is confirmed.

Not assumed: anything about recording. Whether the supervisor's and the
agent's MPC legs are in the MPC recording is an open question; the call's
own Decibyl recording is unaffected by this bridge.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from api.services.live_takeover.bridge import (
    BridgeUnavailable,
    SupervisorBridge,
    SupervisorLeg,
)

API_ROOT = "https://api.plivo.com/v1/Account"
#: A1: where a call that is in an MPC says so.
CONTEXT_MPC_NAME = "plivo_mpc_name"
CONTEXT_AGENT_MEMBER = "plivo_mpc_agent_member_id"

COACH = "coach"

#: ``send(method, url, json) -> (status, body)``; the default is aiohttp.
Sender = Callable[[str, str, dict[str, Any] | None], Awaitable[tuple[int, Any]]]


class PlivoError(Exception):
    pass


def _make_sender(auth_id: str, auth_token: str) -> Sender:
    async def send(
        method: str, url: str, body: dict[str, Any] | None
    ) -> tuple[int, Any]:
        import aiohttp

        auth = aiohttp.BasicAuth(auth_id, auth_token)
        timeout = aiohttp.ClientTimeout(total=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.request(method, url, json=body, auth=auth) as resp,
        ):
            try:
                data = await resp.json(content_type=None)
            except Exception:  # noqa: BLE001 - an empty 204 has no JSON
                data = None
            return resp.status, data

    return send


class PlivoMPCBridge(SupervisorBridge):
    name = "plivo_mpc"
    browser_audio = False
    needs_phone = True

    def __init__(
        self,
        *,
        auth_id: str,
        mpc_name: str,
        agent_member_id: str,
        caller_id: str | None,
        send: Sender,
    ):
        self._base = f"{API_ROOT}/{auth_id}/MultiPartyCall/name_{mpc_name}"
        self._agent_member = str(agent_member_id)
        self._caller_id = caller_id
        self._send = send
        self._supervisor_call: str | None = None
        self._supervisor_member: str | None = None

    async def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        status, data = await self._send(method, f"{self._base}{path}", body)
        if status >= 400:
            raise PlivoError(f"Plivo MPC {method} {path} answered {status}")
        return data

    async def _update(self, member_id: str, **fields: Any) -> None:
        await self._call("POST", f"/Participant/{member_id}/", fields)

    async def _member(self) -> str:
        """A4: the supervisor's member id, looked up by their call."""
        if self._supervisor_member:
            return self._supervisor_member
        data = await self._call("GET", "/Participant/") or {}
        for item in data.get("objects") or []:
            if str(item.get("call_uuid")) == str(self._supervisor_call):
                self._supervisor_member = str(item.get("member_id"))
                return self._supervisor_member
        raise PlivoError("The supervisor's leg is not in the call yet")

    async def join(self, mode: str, leg: SupervisorLeg) -> None:
        if not leg.phone:
            raise BridgeUnavailable("A phone number to call you on is needed.")
        data = await self._call(
            "POST",
            "/",
            {
                "role": "Supervisor",
                "from": self._caller_id,
                "to": leg.phone,
                "coach_mode": mode == COACH,
            },
        )
        calls = (data or {}).get("calls") or []
        self._supervisor_call = calls[0].get("call_uuid") if calls else None
        if not self._supervisor_call:
            raise PlivoError("Plivo did not say which call it dialled")

    async def set_mode(self, mode: str) -> None:
        await self._update(await self._member(), coach_mode=mode == COACH)

    async def set_agent_muted(self, muted: bool) -> None:
        await self._update(self._agent_member, mute=bool(muted))

    async def leave(self) -> None:
        try:
            if self._supervisor_call:
                await self._call("DELETE", f"/Participant/{await self._member()}/")
        except Exception as exc:  # noqa: BLE001 - leaving never fails a hand-back
            logger.warning("Plivo MPC: could not remove the supervisor: {}", exc)
        try:
            await self.set_agent_muted(False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Plivo MPC: could not unmute the agent: {}", exc)
        self._supervisor_call = None
        self._supervisor_member = None


def mpc_of(workflow_run: Any) -> tuple[str, str] | None:
    """A1: the MPC this call is in, or None."""
    context = getattr(workflow_run, "gathered_context", None) or {}
    name = context.get(CONTEXT_MPC_NAME)
    member = context.get(CONTEXT_AGENT_MEMBER)
    if not name or not member:
        return None
    return str(name), str(member)


async def for_run(
    workflow_run: Any, organization_id: int, *, send: Sender | None = None
) -> PlivoMPCBridge:
    found = mpc_of(workflow_run)
    if found is None:
        raise BridgeUnavailable(
            "This call isn't in a conference a phone can join, so it can't be "
            "joined by phone."
        )
    mpc_name, agent_member = found
    from api.services.telephony.factory import get_telephony_provider_for_run
    from api.services.telephony.providers.plivo.provider import PlivoProvider

    provider = await get_telephony_provider_for_run(workflow_run, organization_id)
    if not isinstance(provider, PlivoProvider):
        raise BridgeUnavailable("This call is not on Plivo.")
    auth_id = provider.auth_id
    auth_token = provider.auth_token
    if not auth_id or not auth_token:
        raise BridgeUnavailable("This call's Plivo account has no credentials.")
    # A3: the supervisor sees the business's own number calling them.
    context = getattr(workflow_run, "initial_context", None) or {}
    call_type = getattr(workflow_run, "call_type", None)
    call_type = getattr(call_type, "value", call_type)
    ours = "called_number" if call_type == "inbound" else "from_number"
    caller_id = context.get(ours) or next(iter(provider.from_numbers or []), None)
    return PlivoMPCBridge(
        auth_id=str(auth_id),
        mpc_name=mpc_name,
        agent_member_id=agent_member,
        caller_id=caller_id,
        send=send or _make_sender(str(auth_id), str(auth_token)),
    )
