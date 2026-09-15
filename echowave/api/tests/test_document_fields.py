"""Read a filed document, ask once, remember what was confirmed (A4).

Eval scenario: ``documents_confirm_fields`` -- the person says yes to the
details shown (with one correction), the fields become confirmed facts,
the expiry becomes three reminders, and the reply says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.evals import judge
from api.services.workflow import document_fields as df

ORG = 7
UUID = "3f2b9c1e-0000-4000-8000-000000000001"


class TestWhatIsRead:
    def test_only_the_asked_fields_and_well_formed_dates(self):
        got = df.clean(
            {
                "document_number": "1234 5678 9012",
                "holder_name": "Meera S",
                "expiry_date": "31/03/2027",
                "issue_date": "2020-01-15",
                "amount": "",
                "policy_number": "null",
                "colour": "blue",
            }
        )
        assert got == {
            "document_number": "1234 5678 9012",
            "holder_name": "Meera S",
            "issue_date": "2020-01-15",
        }

    def test_the_message_masks_numbers_and_asks_once(self):
        body = df.lines_for(
            "Aadhaar - Meera.pdf",
            {"document_number": "1234 5678 9012", "holder_name": "Meera S"},
            UUID,
        )
        assert "XXXX XXXX 9012" in body and "1234 5678" not in body
        assert body.startswith(
            "Here is what I read from Aadhaar - Meera.pdf (ref 3f2b9c1e)"
        )
        assert body.endswith("Correct? Say yes, or tell me what to change.")

    def test_nothing_read_is_said_plainly(self):
        assert (
            df.lines_for("scan.jpg", {}, UUID)
            == "I filed scan.jpg (ref 3f2b9c1e) but could not read any details from it."
        )


@pytest.mark.asyncio
class TestReading:
    async def test_the_accounts_model_reads_the_text_and_a_failure_is_empty(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        complete = AsyncMock(
            return_value=ModelReply(
                text='{"policy_number": "LIC-77", "expiry_date": "2027-03-31"}'
            )
        )
        with (
            patch.object(df.db_client, "async_session", return_value=session),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch("api.services.agent_builder.client.complete", complete),
        ):
            assert await df.extract(ORG, "Policy LIC-77 valid till 31 Mar 2027") == {
                "policy_number": "LIC-77",
                "expiry_date": "2027-03-31",
            }
            complete.side_effect = RuntimeError("model down")
            assert await df.extract(ORG, "anything") == {}
        assert await df.extract(ORG, "   ") == {}

    async def test_a_whatsapp_document_is_asked_about_on_both_channels(self):
        document = SimpleNamespace(
            id=55,
            organization_id=ORG,
            filename="LIC policy.pdf",
            document_uuid=UUID,
            full_text="Policy LIC-77 …",
            custom_metadata={"source": "whatsapp", "from": "+919876543210"},
        )
        record = AsyncMock()
        reply = AsyncMock()
        merge = AsyncMock()
        with (
            patch.object(
                df.db_client, "get_document_by_id", AsyncMock(return_value=document)
            ),
            patch.object(
                df,
                "extract",
                AsyncMock(
                    return_value={
                        "policy_number": "LIC-77",
                        "expiry_date": "2027-03-31",
                    }
                ),
            ),
            patch.object(df.db_client, "merge_document_custom_metadata", merge),
            patch.object(df.agent_timeline, "record", record),
            patch("api.services.messaging.whatsapp_inbound.reply", reply),
        ):
            result = await df.propose(ORG, 55)
        assert result["status"] == "proposed"
        assert merge.await_args.kwargs["patch"][df.PROPOSED_KEY] == {
            "policy_number": "LIC-77",
            "expiry_date": "2027-03-31",
        }
        assert merge.await_args.kwargs["patch"]["kind"] == "insurance"
        body = record.await_args.kwargs["payload"]["body"]
        assert "Policy: LIC-77" in body and "Expires: 2027-03-31" in body
        assert reply.await_args.kwargs == {
            "organization_id": ORG,
            "to": "+919876543210",
            "body": body,
        }

    async def test_another_tenants_document_is_not_read(self):
        document = SimpleNamespace(
            id=55,
            organization_id=99,
            filename="x.pdf",
            document_uuid=UUID,
            full_text="",
            custom_metadata={},
        )
        with patch.object(
            df.db_client, "get_document_by_id", AsyncMock(return_value=document)
        ):
            assert await df.propose(ORG, 55) == {"status": "no_document"}


@pytest.mark.asyncio
class TestConfirming:
    def _document(self, proposed):
        return SimpleNamespace(
            id=55,
            organization_id=ORG,
            filename="LIC policy.pdf",
            document_uuid=UUID,
            custom_metadata={
                df.PROPOSED_KEY: proposed,
                "kind": "insurance",
                "source": "whatsapp",
            },
        )

    async def test_yes_believes_the_fields_and_files_three_reminders(self):
        far = (datetime.now(UTC) + timedelta(days=200)).date().isoformat()
        remember = AsyncMock(return_value=4)
        create_task = AsyncMock()
        with (
            patch.object(
                df.db_client,
                "get_document_by_uuid",
                AsyncMock(
                    return_value=self._document(
                        {"policy_number": "LIC-77", "expiry_date": far}
                    )
                ),
            ),
            patch.object(df.db_client, "remember_organisation_facts", remember),
            patch.object(df.db_client, "create_task", create_task),
            patch.object(df.db_client, "merge_document_custom_metadata", AsyncMock()),
            patch.object(df.agent_timeline, "record", AsyncMock()),
        ):
            result = await df.confirm(ORG, document_uuid=UUID, corrections=None)
        assert result["status"] == "success"
        kwargs = remember.await_args.kwargs
        assert (
            kwargs["status"] == "confirmed"
            and kwargs["subject_type"] == "document"
            and kwargs["subject_key"] == UUID
        )
        assert (
            kwargs["facts"]["policy_number"] == "LIC-77"
            and kwargs["facts"]["kind"] == "insurance"
        )
        assert create_task.await_count == 3
        titles = [c.kwargs["title"] for c in create_task.await_args_list]
        assert titles[0].endswith("(60 days)") and titles[2].endswith("(7 days)")
        assert all(
            c.kwargs["status"] == "todo" and c.kwargs["due_at"]
            for c in create_task.await_args_list
        )
        assert len(result["reminders"]) == 3

    async def test_a_correction_wins_and_a_near_expiry_files_fewer(self):
        soon = (datetime.now(UTC) + timedelta(days=20)).date().isoformat()
        remember = AsyncMock(return_value=2)
        create_task = AsyncMock()
        with (
            patch.object(
                df.db_client,
                "get_document_by_uuid",
                AsyncMock(
                    return_value=self._document(
                        {"holder_name": "Meera S", "expiry_date": "2027-01-01"}
                    )
                ),
            ),
            patch.object(df.db_client, "remember_organisation_facts", remember),
            patch.object(df.db_client, "create_task", create_task),
            patch.object(df.db_client, "merge_document_custom_metadata", AsyncMock()),
            patch.object(df.agent_timeline, "record", AsyncMock()),
        ):
            result = await df.confirm(
                ORG,
                document_uuid=UUID,
                corrections={"expiry_date": soon, "holder_name": "Meera Sharma"},
            )
        assert remember.await_args.kwargs["facts"]["holder_name"] == "Meera Sharma"
        assert remember.await_args.kwargs["facts"]["expiry_date"] == soon
        # 60 and 30 days before are already past; only the 7-day one is filed.
        assert create_task.await_count == 1 and len(result["reminders"]) == 1

    async def test_nothing_proposed_is_nothing_to_confirm(self):
        with patch.object(
            df.db_client,
            "get_document_by_uuid",
            AsyncMock(return_value=self._document({})),
        ):
            result = await df.confirm(ORG, document_uuid=UUID, corrections=None)
        assert result["status"] == "error"

    async def test_a_short_reference_from_the_thread_is_resolved(self):
        with (
            patch.object(
                df.db_client, "get_document_by_uuid", AsyncMock(return_value=None)
            ),
            patch.object(
                df.db_client,
                "find_document_by_uuid_prefix",
                AsyncMock(return_value=self._document({"amount": "₹12,000"})),
            ) as by_prefix,
            patch.object(
                df.db_client, "remember_organisation_facts", AsyncMock(return_value=1)
            ),
            patch.object(df.db_client, "create_task", AsyncMock()),
            patch.object(df.db_client, "merge_document_custom_metadata", AsyncMock()),
            patch.object(df.agent_timeline, "record", AsyncMock()),
        ):
            result = await df.confirm(ORG, document_uuid="3f2b9c1e", corrections=None)
        assert result["status"] == "success"
        assert by_prefix.await_args.args[0] == "3f2b9c1e"


@pytest.mark.asyncio
class TestTheDailySweep:
    async def test_one_message_per_account_on_the_thread_and_whatsapp(self):
        now = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
        tasks = [
            SimpleNamespace(
                id=1,
                organization_id=ORG,
                title="LIC policy.pdf expires on 2026-11-14 (60 days)",
                due_at=now,
            ),
            SimpleNamespace(
                id=2,
                organization_id=ORG,
                title="Passport.pdf expires on 2026-11-14 (60 days)",
                due_at=now,
            ),
            SimpleNamespace(
                id=3,
                organization_id=8,
                title="Rent.pdf expires on 2026-11-14 (60 days)",
                due_at=now,
            ),
        ]
        record = AsyncMock()
        reply = AsyncMock()
        with (
            patch.object(
                df.db_client, "tasks_due_between", AsyncMock(return_value=tasks)
            ),
            patch.object(
                df, "_already_sent_today", AsyncMock(side_effect=[False, True])
            ),
            patch(
                "api.services.workflow.documents.own_channels",
                AsyncMock(return_value=({"+919876543210"}, set())),
            ),
            patch.object(df.agent_timeline, "record", record),
            patch("api.services.messaging.whatsapp_inbound.reply", reply),
        ):
            told = await df.remind_due(now)
        assert told == 1
        body = record.await_args.kwargs["payload"]["body"]
        assert (
            body.startswith("Due today:")
            and "LIC policy.pdf" in body
            and "Passport.pdf" in body
        )
        assert (
            reply.await_count == 1 and reply.await_args.kwargs["to"] == "+919876543210"
        )

    async def test_nothing_due_sends_nothing(self):
        with (
            patch.object(df.db_client, "tasks_due_between", AsyncMock(return_value=[])),
            patch.object(df.agent_timeline, "record", AsyncMock()) as record,
        ):
            assert await df.remind_due(datetime.now(UTC)) == 0
        assert record.await_count == 0


@pytest.mark.asyncio
class TestEvalScenario:
    async def test_documents_confirm_fields(self):
        """The person answers the "correct?" with a correction; Decibyl calls
        confirm_document with only that field and reports the reminders."""
        from contextlib import ExitStack

        from api.services.workflow import actions, connected_tools, decibyl

        ask = "yes, but the expiry is 2027-03-31"
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        turn1 = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=df.TOOL_NAME,
                    arguments={
                        "document_uuid": "3f2b9c1e",
                        "corrections": {"expiry_date": "2027-03-31"},
                    },
                ),
            ),
        )
        turn2 = ModelReply(
            text="Remembered 3 details from LIC policy.pdf; reminders on 2027-01-30, 2027-03-01, 2027-03-24."
        )
        confirm = AsyncMock(
            return_value={
                "status": "success",
                "note": "Remembered 3 details from LIC policy.pdf; reminders on 2027-01-30, 2027-03-01, 2027-03-24.",
                "fields": {},
                "reminders": ["2027-01-30", "2027-03-01", "2027-03-24"],
            }
        )
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
                                payload={"body": ask},
                                summary=ask,
                                at=datetime.now(UTC),
                            )
                        ]
                    ),
                ),
                patch(
                    "api.services.workflow.decibyl.db_client.async_session",
                    return_value=session,
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
                    "api.services.workflow.decibyl.agent_timeline.record",
                    new=AsyncMock(),
                ),
                patch(
                    "api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()
                ),
                patch.object(
                    connected_tools, "list_for_organization", AsyncMock(return_value=[])
                ),
                patch(
                    "api.services.agent_builder.client.stream",
                    new=AsyncMock(side_effect=[turn1, turn2]),
                ),
                patch.object(df, "confirm", confirm),
                patch.object(actions, "propose", AsyncMock()),
            ):
                stack.enter_context(p)
            body = await decibyl.answer(ORG, ask)
        assert confirm.await_args.kwargs == {
            "document_uuid": "3f2b9c1e",
            "corrections": {"expiry_date": "2027-03-31"},
        }
        verdict = judge.phrase_checks(
            [{"role": "user", "text": ask}, {"role": "agent", "text": body}],
            must_say=["Remembered", "reminders"],
            must_not_say=["could not"],
        )
        assert verdict is None, verdict
