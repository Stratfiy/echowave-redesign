"""How the supervisor's voice reaches the caller: one interface, two ways.

The controller on the call's worker decides *when* the agent is silenced
and the supervisor heard; a bridge decides *how* the supervisor's voice gets
onto the line, and whether the telephony itself has to change. Swapping one
for another is a setting (``LIVE_TAKEOVER_BRIDGE``), not a code change:

- ``PipelineBridge`` ("pipeline", the default): nothing about the call's
  telephony changes. The supervisor talks into the browser; their audio
  comes over a WebSocket and is played into the call's own output by the
  ``OutputGate``. Works on every transport (Plivo, any other provider, a web
  call) and is what the tests run end to end.
- ``PlivoMPCBridge`` ("plivo_mpc", ``plivo_mpc.py``): the supervisor's phone
  is dialled into the Plivo Multi-Party Call the caller is in, and Plivo
  mixes. The agent is muted on its MPC leg as well as gated in the pipeline.
  Its assumptions about Plivo are unconfirmed and listed in that module.

A bridge is told about every change; one that has nothing to do for a change
(the pipeline bridge, for all of them) does nothing.

**Phone calls and the pipeline bridge.** Mixing a supervisor's browser audio
into a phone (PSTN) call on Decibyl's servers is held back until telecom
counsel confirms it (``constants.ALLOW_SERVER_MIXED_PSTN_BARGE``, off): on a
phone call the pipeline bridge carries no voice, so barging in is refused
with ``PSTN_VOICE_OFF`` and a take-over is silent -- the agent stops, the
supervisor guides it by typing and hands the call back. Web calls are
unaffected. The Plivo conference path is the way a voice will reach a phone
call; it serves only calls already in a conference, and none are yet
(``MPC_NOT_YET``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from loguru import logger

PIPELINE = "pipeline"
PLIVO_MPC = "plivo_mpc"
BRIDGES = (PIPELINE, PLIVO_MPC)

#: Run modes that are a web call, not a phone call: the same two the live
#: list shows as "web" (``live_supervision.session._direction``). Anything
#: else counts as a phone call here, so a new telephony mode is held back
#: rather than mixed into by default.
WEB_MODES = frozenset({"webrtc", "smallwebrtc"})
#: The live list's word for a web call (``LiveCall.direction``).
WEB = "web"

#: Said to a supervisor who tries to speak into a phone call from the browser.
PSTN_VOICE_OFF = (
    "Speaking into phone calls from the browser is off until it is cleared "
    "with telecom counsel. You can take the call over: the agent goes quiet, "
    "you guide it by typing, and then hand the call back."
)
#: Shown with the Plivo conference bridge, which cannot reach today's calls.
MPC_NOT_YET = (
    "Joining by phone works only for calls placed in a Plivo conference, and "
    "calls are not placed in one yet, so this call can't be joined by phone."
)


class BridgeUnavailable(Exception):
    """This call cannot be joined this way (said to the person joining)."""


@dataclass(frozen=True)
class SupervisorLeg:
    by: str
    by_user_id: int
    #: Only for a bridge that dials the supervisor (``needs_phone``).
    phone: str | None = None


class SupervisorBridge(ABC):
    name: str = ""
    #: The supervisor's voice arrives as microphone packets from a browser.
    browser_audio: bool = False
    #: Joining needs a number to call the supervisor on.
    needs_phone: bool = False

    @abstractmethod
    async def join(self, mode: str, leg: SupervisorLeg) -> None:
        """Put the supervisor on the line in ``mode`` (barge or takeover)."""

    @abstractmethod
    async def set_mode(self, mode: str) -> None:
        """Barge to take-over or back, with the supervisor already on."""

    @abstractmethod
    async def set_agent_muted(self, muted: bool) -> None:
        """Whether the agent's own leg is heard, where it has one."""

    @abstractmethod
    async def leave(self) -> None:
        """Take the supervisor off the line. Never raises."""


class PipelineBridge(SupervisorBridge):
    """The browser's audio through the call's own output: the gates do it
    all, so there is nothing to tell a telephony provider.

    ``browser_audio`` is False on a phone call while server-side mixing is
    held back (see the module docstring): the gates still silence the agent,
    and the supervisor's microphone is not played."""

    name = PIPELINE

    def __init__(self, *, browser_audio: bool = True):
        self.browser_audio = bool(browser_audio)

    async def join(self, mode: str, leg: SupervisorLeg) -> None:
        return None

    async def set_mode(self, mode: str) -> None:
        return None

    async def set_agent_muted(self, muted: bool) -> None:
        return None

    async def leave(self) -> None:
        return None


def configured() -> str:
    from api import constants

    name = str(getattr(constants, "LIVE_TAKEOVER_BRIDGE", PIPELINE) or PIPELINE)
    if name not in BRIDGES:
        logger.warning(
            "LIVE_TAKEOVER_BRIDGE={!r} is not one of {}; using the pipeline",
            name,
            BRIDGES,
        )
        return PIPELINE
    return name


def is_web_call(workflow_run: Any) -> bool:
    return str(getattr(workflow_run, "mode", "") or "") in WEB_MODES


def browser_voice_allowed(*, web_call: bool) -> bool:
    """Whether the pipeline bridge may play a browser's audio into this call."""
    from api import constants

    return web_call or bool(constants.ALLOW_SERVER_MIXED_PSTN_BARGE)


async def make(
    name: str, *, workflow_run: Any, organization_id: int
) -> SupervisorBridge:
    """The bridge for one call. Raises ``BridgeUnavailable`` when the
    configured one cannot serve this call."""
    if name == PLIVO_MPC:
        from api.services.live_takeover import plivo_mpc

        return await plivo_mpc.for_run(workflow_run, organization_id)
    return PipelineBridge(
        browser_audio=browser_voice_allowed(web_call=is_web_call(workflow_run))
    )
