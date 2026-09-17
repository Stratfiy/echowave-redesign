"""One request, one card.

Asked once to draft a reply, Decibyl put two identical cards on the thread:
same tool, same arguments, same label, same effect line. Only one was still
pending by the time anybody looked, so nothing was run twice -- but two live
cards for one intent is two confirmations for one act, and on a send that is
two emails.

``propose`` recorded unconditionally. The dispatch loop iterates over
``reply.tool_calls`` and proposes for each, and nothing between them asked
whether the thread already had that exact proposal waiting. A model that
emits the same call twice in one round is not misbehaving in a way we can
prevent; writing the second row is.

So a proposal identical to one already waiting is not written again. The
model is told it is already there, which is the true answer and the one that
stops it announcing two cards.

Identical means the same action with the same arguments, still proposed.
A second card that differs in any argument is a different act and is
written: the same tool called twice with two recipients is two sends
somebody may well want.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.enums import AgentEventKind
from api.services.workflow import actions


def _pending(action="run_tool", **args):
    return SimpleNamespace(
        id=1,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        payload={
            "action": action,
            "args": args or {"tool_uuid": "u-1"},
            "state": "proposed",
        },
    )


def _resolved(action="run_tool", **args):
    return {
        "action": action,
        "args": args or {"tool_uuid": "u-1"},
        "label": "Gmail — Send Email via gmail",
        "why": "Asked in the thread",
        "effect": "Runs in gmail and reaches people there. It cannot be undone.",
        "reversible": False,
        "state": "proposed",
    }


class TestASecondIdenticalCardIsNotWritten:
    async def test_it_is_not_recorded_again(self):
        with (
            patch.object(actions, "resolve", AsyncMock(return_value=_resolved())),
            patch.object(
                actions, "_already_proposed", AsyncMock(return_value=_pending())
            ),
            patch.object(actions.agent_timeline, "record", AsyncMock()) as recorded,
        ):
            out = await actions.propose(
                organization_id=1,
                workflow_id=None,
                workflow_run_id=None,
                arguments={"action": "run_tool", "tool_uuid": "u-1"},
            )
        recorded.assert_not_awaited()
        assert out["status"] == "already_proposed"

    async def test_the_model_is_told_it_is_already_there(self):
        """So it says "I've proposed it" once, not twice."""
        with (
            patch.object(actions, "resolve", AsyncMock(return_value=_resolved())),
            patch.object(
                actions, "_already_proposed", AsyncMock(return_value=_pending())
            ),
            patch.object(actions.agent_timeline, "record", AsyncMock()),
        ):
            out = await actions.propose(
                organization_id=1,
                workflow_id=None,
                workflow_run_id=None,
                arguments={"action": "run_tool", "tool_uuid": "u-1"},
            )
        assert "already" in out["note"].lower()


class TestWhatIsStillWritten:
    async def test_the_first_card_is_written(self):
        with (
            patch.object(actions, "resolve", AsyncMock(return_value=_resolved())),
            patch.object(actions, "_already_proposed", AsyncMock(return_value=None)),
            patch.object(actions.agent_timeline, "record", AsyncMock()) as recorded,
        ):
            out = await actions.propose(
                organization_id=1,
                workflow_id=None,
                workflow_run_id=None,
                arguments={"action": "run_tool", "tool_uuid": "u-1"},
            )
        recorded.assert_awaited()
        assert out["status"] == "proposed"

    async def test_a_lookup_that_fails_does_not_lose_the_card(self):
        """The dedupe is a convenience. A card not written because the check
        broke is a person who asked for something and got nothing."""
        with (
            patch.object(actions, "resolve", AsyncMock(return_value=_resolved())),
            patch.object(
                actions, "_already_proposed", AsyncMock(side_effect=RuntimeError("db"))
            ),
            patch.object(actions.agent_timeline, "record", AsyncMock()) as recorded,
        ):
            out = await actions.propose(
                organization_id=1,
                workflow_id=None,
                workflow_run_id=None,
                arguments={"action": "run_tool", "tool_uuid": "u-1"},
            )
        recorded.assert_awaited()
        assert out["status"] == "proposed"


class TestWhatCountsAsTheSameCard:
    def test_same_action_and_arguments(self):
        assert actions._is_same_proposal(
            _pending(tool_uuid="u-1"), _resolved(tool_uuid="u-1")
        )

    def test_a_different_argument_is_a_different_act(self):
        """The same tool with two recipients is two sends, and somebody may
        well want both."""
        assert not actions._is_same_proposal(
            _pending(tool_uuid="u-1", to="a@x.com"),
            _resolved(tool_uuid="u-1", to="b@x.com"),
        )

    def test_a_different_action_is_not_the_same(self):
        assert not actions._is_same_proposal(
            _pending(action="run_tool"), _resolved(action="build_from_spec")
        )

    def test_a_settled_card_does_not_block_a_new_one(self):
        """Confirmed or declined, it is finished, and asking again is a new
        request -- otherwise declining once would bar the act forever."""
        settled = _pending()
        settled.payload["state"] = "declined"
        assert not actions._is_same_proposal(settled, _resolved())
