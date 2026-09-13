"""What the business has confirmed has to arrive in the prompt.

`organisation_memory` was complete and dead. Facts were extracted from calls,
promoted, scoped per bot, confirm-gated and corroboration-counted — and
`merge_for_prompt`, `recall_for_bot` and `recall_for_subject` had no callers
anywhere but their own tests. The business learned, and no agent ever read any
of it back.

That is the shape `api/AGENTS.md` names, one layer up: not a filter dropping
rows, but a whole reader nobody wired in. It produces exactly nothing, with no
error and no log line, and the only symptom is an agent asking a question its
own account answered last month.

So these tests are mostly about *arrival*. The rendering is easy; the thing
worth guarding is that the block leaves the engine and lands in the string the
model is sent, and that the confirm gate is still what decides which facts get
that far.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.organisation_memory import (
    MAX_REMEMBERED,
    recall_for_bot,
    remembered_block,
)


class TestTheBlockItself:
    def test_it_states_the_facts(self):
        block = remembered_block({"opening hours": "9 to 7, closed Sunday"})
        assert "opening hours: 9 to 7, closed Sunday" in block

    def test_nothing_remembered_is_no_block_at_all(self):
        # Not an empty heading: a business that has confirmed nothing should
        # not have a paragraph in every prompt announcing that it knows nothing.
        assert remembered_block({}) is None
        assert remembered_block(None) is None

    def test_a_blank_value_is_not_a_line(self):
        assert remembered_block({"opening hours": "   "}) is None

    def test_it_tells_the_model_not_to_ask_again(self):
        """The whole reason this exists. A node prompt is an unconditional
        instruction — "ask what hours suit them" — and it outranks a table the
        model could have consulted. Facts have to be stated, not available."""
        block = remembered_block({"opening hours": "9 to 7"})
        assert "do not ask" in block.lower()

    def test_it_tells_the_model_not_to_argue_with_a_caller(self):
        # An agent that contradicts a caller using the business's own record is
        # worse than one that quietly takes the correction: the caller is on
        # the phone and the record was right last month.
        block = remembered_block({"delivery area": "within 10km"})
        assert "do not argue" in block.lower()

    def test_it_caps_what_reaches_one_prompt(self):
        # Every line is tokens on every turn of every call.
        many = {f"key {i}": f"value {i}" for i in range(MAX_REMEMBERED + 25)}
        assert remembered_block(many).count("\n- ") == MAX_REMEMBERED

    def test_the_key_is_shown_as_written(self):
        """These were composed to be read. A prompt is the one place the
        machine-readable spelling has no advantage."""
        assert "cancellation policy:" in remembered_block(
            {"cancellation policy": "24 hours"}
        )


class TestOnlyConfirmedFactsGetThisFar:
    """The gate that makes the whole table safe to write to on every call.

    Everything inferred from a conversation arrives as `learned`. An agent that
    starts telling callers something it merely overheard is the failure that
    would cost an account, and no corroboration count substitutes for a person
    saying yes.
    """

    @pytest.mark.asyncio
    async def test_it_asks_for_confirmed_facts_and_nothing_else(self):
        with patch("api.services.workflow.organisation_memory.db_client") as mock_db:
            mock_db.organisation_memory = AsyncMock(return_value=[])
            await recall_for_bot(organization_id=7, workflow_id=3)

        asked = mock_db.organisation_memory.await_args.kwargs
        assert asked["status"] == "confirmed"
        # Gaps are the other half of this table: things nobody could answer.
        # An agent reading its own gap list announces what it does not know.
        assert asked["kind"] == "fact"
        assert asked["organization_id"] == 7
        assert asked["workflow_id"] == 3

    @pytest.mark.asyncio
    async def test_a_failure_to_read_memory_is_not_a_failed_call(self):
        with patch("api.services.workflow.organisation_memory.db_client") as mock_db:
            mock_db.organisation_memory = AsyncMock(side_effect=RuntimeError("down"))
            assert await recall_for_bot(organization_id=7, workflow_id=3) == {}


class TestItActuallyReachesThePrompt:
    """The half that was missing. Everything above here was already true while
    no agent read a single fact."""

    def _node(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            prompt="Greet the caller and book them in.",
            add_global_prompt=False,
            node_type=None,
            document_uuids=[],
        )

    def _compose(self, **kwargs):
        from types import SimpleNamespace

        from api.services.workflow.pipecat_engine_context_composer import (
            compose_system_prompt_for_node,
        )

        return compose_system_prompt_for_node(
            node=self._node(),
            workflow=SimpleNamespace(global_node_id=None, nodes={}),
            format_prompt=lambda text: text,
            has_recordings=False,
            today_line="Today is Sunday.",
            **kwargs,
        )

    def test_the_block_lands_in_the_system_prompt(self):
        prompt = self._compose(remembered="WHAT THIS BUSINESS HAS CONFIRMED.\n- x: y")
        assert "WHAT THIS BUSINESS HAS CONFIRMED." in prompt
        assert "- x: y" in prompt

    def test_no_memory_adds_nothing(self):
        assert "CONFIRMED" not in self._compose(remembered=None)
        assert "CONFIRMED" not in self._compose(remembered="")

    def test_it_sits_above_the_caller_s_own_answers(self):
        """Placement is load-bearing twice over.

        For caching: this block is byte-identical for the life of a call and
        the known-values block is not, so anything stable that sat below a
        changing block would stop being cacheable.

        For meaning: a caller's answer is the most recent thing said and should
        read that way. Settled facts about the business below it would look
        like the older, weaker claim.
        """
        prompt = self._compose(
            remembered="WHAT THIS BUSINESS HAS CONFIRMED.\n- hours: 9 to 7",
            known_values={"caller_name": "Ramesh"},
        )
        assert prompt.index("CONFIRMED") < prompt.index("Ramesh")

    def test_it_sits_below_the_operator_s_own_prompt(self):
        # The operator wrote the instruction; this is reference material for
        # carrying it out, not a replacement for it.
        prompt = self._compose(remembered="WHAT THIS BUSINESS HAS CONFIRMED.\n- a: b")
        assert prompt.index("book them in") < prompt.index("CONFIRMED")


class TestTheEngineReadsItOncePerCall:
    """Two costs, both paid per node transition if this is not cached.

    A database round trip on the path between a caller finishing a sentence and
    the agent starting one; and a prompt prefix that changes between nodes,
    which throws away the provider-side cache that was 9,088 of 9,608 prompt
    tokens on a real call here.
    """

    @pytest.mark.asyncio
    async def test_a_second_node_does_not_query_again(self):
        from api.services.workflow.pipecat_engine import PipecatEngine

        engine = PipecatEngine.__new__(PipecatEngine)
        engine._workflow_run_id = 1
        engine._organization_id = 7
        engine._workflow_id = 3
        engine._remembered_block = None

        with patch(
            "api.services.workflow.organisation_memory.recall_for_bot",
            new=AsyncMock(return_value={"hours": "9 to 7"}),
        ) as recall:
            first = await engine._get_remembered_block()
            second = await engine._get_remembered_block()

        assert "hours: 9 to 7" in first
        assert first == second
        assert recall.await_count == 1

    @pytest.mark.asyncio
    async def test_an_empty_memory_is_not_re_queried_either(self):
        """`None` means "not looked yet" and `""` means "looked, found
        nothing". Collapsing the two would re-query on every transition for
        exactly the accounts that have nothing to gain from it."""
        from api.services.workflow.pipecat_engine import PipecatEngine

        engine = PipecatEngine.__new__(PipecatEngine)
        engine._workflow_run_id = 1
        engine._organization_id = 7
        engine._workflow_id = 3
        engine._remembered_block = None

        with patch(
            "api.services.workflow.organisation_memory.recall_for_bot",
            new=AsyncMock(return_value={}),
        ) as recall:
            assert await engine._get_remembered_block() == ""
            assert await engine._get_remembered_block() == ""

        assert recall.await_count == 1


class TestALookupThatFailsDoesNotFailTheCall:
    @pytest.mark.asyncio
    async def test_a_failed_run_lookup_is_an_empty_block(self):
        """`recall_for_bot` guards its own read; the run-to-bot lookup before
        it did not, and it raises inside set_node -- on the path that starts
        the conversation. The first thing that did in CI was fail a pipeline
        test whose fake run id matched no row, and then hang the run."""
        from api.services.workflow.pipecat_engine import PipecatEngine

        engine = PipecatEngine.__new__(PipecatEngine)
        engine._workflow_run_id = 1
        engine._organization_id = 7
        engine._workflow_id = None
        engine._remembered_block = None

        with patch(
            "api.services.workflow.pipecat_engine.db_client."
            "get_workflow_id_by_workflow_run_id",
            new=AsyncMock(side_effect=ConnectionRefusedError("no database")),
        ) as lookup:
            assert await engine._get_remembered_block() == ""
            # Looked, failed, held: a call does not retry the database on
            # every node transition either.
            assert await engine._get_remembered_block() == ""
        assert lookup.await_count == 1
