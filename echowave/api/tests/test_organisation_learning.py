"""What the business learns from what its agents did.

The rule every test here circles is the one that makes this safe to run on
every call: nothing learned from a conversation reaches an agent's prompt until
a person confirms it. An agent that starts confidently telling callers
something it merely overheard is the failure that costs an account, and no
number of corroborations substitutes for somebody saying yes.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.organisation_memory import (
    FactsRequest,
    StatusRequest,
    read_memory,
    set_status,
    write_facts,
)
from api.services.workflow import organisation_learning as learning


def _interaction(status="success", app="googlecalendar", name="book_appointment"):
    return SimpleNamespace(status=status, app=app, name=name)


def _user(org=42):
    return SimpleNamespace(selected_organization_id=org)


class TestWhatACallTeaches:
    def test_a_call_that_went_nowhere_is_recorded_as_a_gap(self):
        """The most valuable signal in the product: a rising count here is the
        agent failing to understand people, which no outcome rate reveals."""
        observations = learning.observations_from_run(intent=learning.NO_INTENT)
        assert observations[0]["kind"] == learning.KIND_GAP
        assert observations[0]["key"] == learning.GAP_NOT_UNDERSTOOD

    def test_the_callers_own_question_becomes_the_gap(self):
        observations = learning.observations_from_run(
            intent=learning.NO_INTENT,
            gathered_context={
                "extracted_variables": {"caller_question": "Do you open on Saturday?"}
            },
        )
        assert observations[0]["value"] == "Do you open on Saturday?"

    def test_a_gap_with_no_question_is_still_counted_and_never_invented(self):
        """It says the agent lost a caller, which is worth knowing. Guessing
        the subject would put words in somebody's mouth on a screen an operator
        acts on."""
        observations = learning.observations_from_run(intent=learning.NO_INTENT)
        assert "did not understand" in observations[0]["value"]

    def test_a_handover_to_a_person_is_a_gap_not_a_failure(self):
        """Handing a real problem to a person is correct behaviour. Forty of
        the same handover is a job still being done by hand."""
        observations = learning.observations_from_run(escalated=True)
        assert observations[0]["key"] == learning.GAP_ESCALATED

    def test_a_tool_that_failed_teaches_the_business_its_system_is_unreachable(self):
        observations = learning.observations_from_run(
            interactions=[_interaction(status="error", app="googlecalendar")]
        )
        assert observations[0]["key"] == learning.GAP_APP_FAILED
        assert observations[0]["value"] == "googlecalendar"

    def test_a_call_that_worked_teaches_nothing_and_writes_nothing(self):
        assert (
            learning.observations_from_run(
                intent="Take the booking details",
                interactions=[_interaction()],
            )
            == []
        )

    def test_the_same_gap_twice_in_one_call_is_one_gap(self):
        """A caller who asked the same unanswerable question twice has not
        doubled the problem."""
        observations = learning.observations_from_run(
            interactions=[
                _interaction(status="error", app="googlecalendar"),
                _interaction(status="error", app="googlecalendar"),
            ]
        )
        assert len(observations) == 1

    def test_a_long_question_is_trimmed_to_something_a_screen_can_list(self):
        observations = learning.observations_from_run(
            intent=learning.NO_INTENT,
            gathered_context={"extracted_variables": {"caller_query": "x" * 500}},
        )
        assert len(observations[0]["value"]) <= learning.MAX_GAP_CHARS


class TestLearningNeverBreaksACall:
    @pytest.mark.asyncio
    async def test_a_database_failure_is_swallowed_not_raised(self):
        """This runs after the caller has already been answered, so there is
        nothing a failure could usefully interrupt -- and an exception escaping
        here would take the run's other post-call bookkeeping with it."""
        with patch(
            "api.services.workflow.organisation_learning.db_client"
            ".remember_organisation_observations",
            AsyncMock(side_effect=RuntimeError("database on fire")),
        ):
            assert (
                await learning.learn_from_run(
                    organization_id=1,
                    workflow_run_id=9,
                    intent=learning.NO_INTENT,
                )
                == 0
            )

    @pytest.mark.asyncio
    async def test_nothing_learned_means_nothing_written(self):
        with patch(
            "api.services.workflow.organisation_learning.db_client"
            ".remember_organisation_observations",
            AsyncMock(),
        ) as write:
            await learning.learn_from_run(
                organization_id=1, workflow_run_id=9, intent="Take the booking details"
            )
        write.assert_not_called()


def _row(id=1, kind="gap", status="learned", value="Do you open on Saturday?"):
    return SimpleNamespace(
        id=id,
        kind=kind,
        subject_key="not_understood",
        key="do_you_open_on_saturday",
        value=value,
        status=status,
        times_seen=12,
        first_seen_at=None,
        last_seen_at=None,
        source_run_id=7,
    )


class TestTheMemoryScreen:
    @pytest.mark.asyncio
    async def test_facts_and_gaps_come_back_separated(self):
        rows = [_row(1, kind="gap"), _row(2, kind="fact", value="Mon-Sat 9:30-8")]
        with patch(
            "api.routes.organisation_memory.db_client.organisation_memory",
            AsyncMock(return_value=rows),
        ):
            response = await read_memory(user=_user())
        assert [item.id for item in response.gaps] == [1]
        assert [item.id for item in response.facts] == [2]

    @pytest.mark.asyncio
    async def test_something_dismissed_stays_off_the_screen(self):
        """A list that will not stay dismissed is a list people stop reading."""
        with patch(
            "api.routes.organisation_memory.db_client.organisation_memory",
            AsyncMock(return_value=[_row(status="rejected")]),
        ):
            response = await read_memory(user=_user())
        assert response.facts == [] and response.gaps == []

    @pytest.mark.asyncio
    async def test_what_the_business_types_is_believed_immediately(self):
        """The person filling the onboarding form is the same person who would
        confirm it afterwards. Asking twice is ceremony."""
        with (
            patch(
                "api.routes.organisation_memory.db_client.remember_organisation_facts",
                AsyncMock(return_value=1),
            ) as write,
            patch(
                "api.routes.organisation_memory.db_client.organisation_memory",
                AsyncMock(return_value=[]),
            ),
        ):
            await write_facts(
                FactsRequest(facts={"opening_hours": "Mon-Sat 9:30-8", "blank": "  "}),
                user=_user(),
            )
        assert write.await_args.kwargs["status"] == "confirmed"
        # An empty answer is not a fact. Writing it would have the agent
        # confidently state a blank.
        assert write.await_args.kwargs["facts"] == {"opening_hours": "Mon-Sat 9:30-8"}

    @pytest.mark.asyncio
    async def test_an_unknown_status_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await set_status(
                fact_id=1, request=StatusRequest(status="maybe"), user=_user()
            )
        assert raised.value.status_code == 400

    @pytest.mark.asyncio
    async def test_confirming_somebody_elses_fact_is_a_404_not_a_403(self):
        """Saying "you may not touch this" would confirm another account holds
        that id. The scoping is in the UPDATE, so a miss is indistinguishable
        from a deleted row, which is the point."""
        with patch(
            "api.routes.organisation_memory.db_client.set_organisation_fact_status",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(HTTPException) as raised:
                await set_status(
                    fact_id=99, request=StatusRequest(status="confirmed"), user=_user()
                )
        assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_session_with_no_organization_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await read_memory(user=_user(org=None))
        assert raised.value.status_code == 400
