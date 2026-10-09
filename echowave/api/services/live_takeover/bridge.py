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
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from loguru import logger

PIPELINE = "pipeline"
PLIVO_MPC = "plivo_mpc"
BRIDGES = (PIPELINE, PLIVO_MPC)


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
    all, so there is nothing to tell a telephony provider."""

    name = PIPELINE
    browser_audio = True

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


async def make(
    name: str, *, workflow_run: Any, organization_id: int
) -> SupervisorBridge:
    """The bridge for one call. Raises ``BridgeUnavailable`` when the
    configured one cannot serve this call."""
    if name == PLIVO_MPC:
        from api.services.live_takeover import plivo_mpc

        return await plivo_mpc.for_run(workflow_run, organization_id)
    return PipelineBridge()
