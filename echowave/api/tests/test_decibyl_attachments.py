"""A file on the line is the material for the request.

"Build a bot for this" with a document attached used to get "which
template?" back, because the file's text never reached the model. Now it
does, and the eval scenario ``decibyl_builds_from_attached_document``
checks the outcome: the closest template is chosen from the document and
a create_bot card is proposed, without asking which template.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.evals import judge
from api.services.workflow import actions, connected_tools, decibyl

ORG = 7
BRIEF = (
    "Agentic voice AI workflow for Elock Clinic. The assistant answers the "
    "clinic's phone, greets the caller, books and reschedules appointments "
    "with Dr Rao, confirms the patient's name and phone number, and hands "
    "emergencies to the front desk. Hours 9 to 6, closed Sundays."
)
ATTACHMENT = {
    "document_uuid": "doc-1",
    "filename": "Agentic_Voice_AI_Workflow_Elock.docx",
}


class TestTheAttachedBlock:
    async def test_the_files_text_joins_the_context(self):
        document = SimpleNamespace(full_text=BRIEF)
        with patch.object(
            db_client, "get_document_by_uuid", AsyncMock(return_value=document)
        ):
            block = await decibyl.attached_block(ORG, [ATTACHMENT])
        assert block.startswith("## Attached to this line")
        assert "Elock.docx" in block and "Dr Rao" in block

    async def test_a_file_still_being_read_is_named_as_such(self):
        document = SimpleNamespace(full_text="")
        with (
            patch.object(
                db_client, "get_document_by_uuid", AsyncMock(return_value=document)
            ),
            patch.object(decibyl, "ATTACHMENT_WAIT_SECONDS", 0),
            patch("asyncio.sleep", AsyncMock()),
        ):
            block = await decibyl.attached_block(ORG, [ATTACHMENT])
        assert "still being read" in block

    async def test_no_attachments_no_block(self):
        assert await decibyl.attached_block(ORG, []) == ""
        assert await decibyl.attached_block(ORG, None) == ""

    async def test_a_long_file_is_clipped(self):
        document = SimpleNamespace(full_text="x" * 50_000)
        with patch.object(
            db_client, "get_document_by_uuid", AsyncMock(return_value=document)
        ):
            block = await decibyl.attached_block(ORG, [ATTACHMENT])
        assert len(block) < decibyl.ATTACHMENT_CHARS + 200


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@contextmanager
def _thread(last_line: str):
    with ExitStack() as stack:
        for p in (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value="## Team\nnothing"),
            ),
            patch(
                "api.services.workflow.decibyl.office_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            actor="human",
                            payload={"body": last_line},
                            summary=last_line,
                            at=datetime.now(UTC),
                        )
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=_session(),
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch(
                "api.services.knowledge_graph.spaced_recall.related_context",
                AsyncMock(return_value=""),
            ),
            patch(
                "api.services.knowledge_graph.decisions.note", AsyncMock(return_value=0)
            ),
            patch(
                "api.services.knowledge_graph.feed.remember_exchange",
                AsyncMock(return_value=False),
            ),
        ):
            stack.enter_context(p)
        yield


def _transcript(user: str, agent: str) -> list[dict]:
    return [{"role": "user", "text": user}, {"role": "agent", "text": agent}]


@pytest.mark.asyncio
class TestEvalScenarios:
    async def test_decibyl_builds_from_attached_document(self):
        ask = "build a bot for this"
        seen: dict = {}

        async def stream(**kwargs):
            # The first turn sees the brief in its context and proposes the bot.
            if not seen:
                messages = kwargs.get("conversation").messages
                seen["context"] = str(messages[-1].get("content"))
                return ModelReply(
                    text="",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name=actions.TOOL_NAME,
                            arguments={
                                "action": actions.CREATE_BOT,
                                "template_id": "clinic_appointment",
                                "name": "Elock Clinic front desk",
                                "variables": {
                                    "clinic_name": "Elock Clinic",
                                    "doctor": "Dr Rao",
                                },
                            },
                        ),
                    ),
                )
            return ModelReply(
                text="From the document: a clinic front desk for Elock Clinic with Dr Rao. I have proposed Elock Clinic front desk from the clinic template; confirm on the card and it is built."
            )

        propose = AsyncMock(
            return_value={
                "status": "proposed",
                "note": "Proposed: Create Elock Clinic front desk.",
            }
        )
        document = SimpleNamespace(full_text=BRIEF)
        with (
            _thread(ask),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                db_client, "get_document_by_uuid", AsyncMock(return_value=document)
            ),
            patch.object(actions, "propose", propose),
        ):
            body = await decibyl.answer(ORG, ask, attachments=[ATTACHMENT])
        assert "Dr Rao" in seen["context"], "the document's text reached the model"
        assert (
            propose.await_args.kwargs["arguments"]["template_id"]
            == "clinic_appointment"
        )
        verdict = judge.phrase_checks(
            _transcript(ask, body),
            must_say=["Elock", "proposed", "card"],
            must_not_say=["which template", "which one"],
        )
        assert verdict is None, verdict
