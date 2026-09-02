"""Plivo frame serializer.

Wraps pipecat's serializer to add the transfer hook its Twilio counterpart
already has. Upstream's ``PlivoFrameSerializer`` hangs the call up on any
``EndFrame`` when ``auto_hang_up`` is set, which is right for every teardown
except one: a transfer ends the *pipeline*, not the *call*, and hanging up
there drops the caller a moment before the human they were promised picks up.

Kept here rather than pushed upstream so the transfer path is owned by the
same repository as the routes and the strategy it depends on.
"""

from typing import Optional

from loguru import logger
from pipecat.frames.frames import CancelFrame, EndFrame, Frame
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy
from pipecat.serializers.plivo import PlivoFrameSerializer as _BasePlivoSerializer
from pipecat.utils.enums import EndTaskReason


class PlivoFrameSerializer(_BasePlivoSerializer):
    """``PlivoFrameSerializer`` that can hand a call to a human.

    Mirrors the shape of pipecat's Twilio serializer: on an ``EndFrame`` or
    ``CancelFrame`` carrying ``EndTaskReason.TRANSFER_CALL``, run the transfer
    strategy instead of the hang-up. Every other frame is the base class's.
    """

    def __init__(
        self,
        *args,
        transfer_strategy: Optional[TransferStrategy] = None,
        hangup_strategy: Optional[HangupStrategy] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._transfer_strategy = transfer_strategy
        self._hangup_strategy = hangup_strategy
        self._transfer_attempted = False

    def _operation_context(self) -> dict:
        """What the strategies need to talk to Plivo about this call."""
        return {
            "call_id": self._call_id,
            "auth_id": self._auth_id,
            "auth_token": self._auth_token,
        }

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (EndFrame, CancelFrame)):
            reason = getattr(frame, "reason", None)

            if (
                reason == EndTaskReason.TRANSFER_CALL.value
                and not self._transfer_attempted
            ):
                # Set before the await: a second EndFrame arriving while the
                # redirect is in flight must not dial the destination twice.
                self._transfer_attempted = True

                if self._transfer_strategy:
                    success = await self._transfer_strategy.execute_transfer(
                        self._operation_context()
                    )
                    if not success:
                        logger.error(
                            f"Transfer strategy failed for Plivo call "
                            f"{self._call_id}"
                        )
                else:
                    logger.warning(
                        f"No transfer strategy configured for Plivo call "
                        f"{self._call_id}"
                    )

                # Return without delegating: the base class would hang up the
                # very leg we have just handed to a conference.
                return None

            if (
                self._params.auto_hang_up
                and self._hangup_strategy
                and not self._hangup_attempted
            ):
                self._hangup_attempted = True
                success = await self._hangup_strategy.execute_hangup(
                    self._operation_context()
                )
                if not success:
                    logger.error(
                        f"Hangup strategy failed for Plivo call {self._call_id}"
                    )
                return None

        return await super().serialize(frame)


__all__ = ["PlivoFrameSerializer"]
