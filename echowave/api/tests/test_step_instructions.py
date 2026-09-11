"""Telling a step it is a step.

Run 313: the caller agreed an 11:00 slot, and the agent -- standing in "Find a
slot", which holds no tools -- promised him a reminder and wished him a good
day. The booking tool lives on the next node. Nothing errored; the caller rang
off believing he had an appointment that was never made.

Nothing in the composed prompt had ever said a node cannot finish a call, so
when the conversation felt complete the model did the human thing.
"""

from api.services.workflow.step_instructions import moving_on_instructions


class _Node:
    def __init__(self, is_end=False, out_edges=(("slot_chosen",),)):
        self.is_end = is_end
        self.out_edges = list(out_edges)


class TestAnOrdinaryStep:
    def test_it_is_told_it_cannot_end_the_call(self):
        text = moving_on_instructions(_Node())
        assert "cannot end the call" in text

    def test_it_is_told_that_saying_is_not_doing(self):
        """The exact failure: it said a reminder would be sent, from a node
        with no tool that could send one."""
        text = moving_on_instructions(_Node())
        assert "is not doing it" in text

    def test_it_is_told_not_to_say_goodbye(self):
        assert "Do not say goodbye" in moving_on_instructions(_Node())


class TestWhereItStaysQuiet:
    def test_an_end_node_is_left_alone(self):
        """Where a call is supposed to finish. Telling it otherwise would
        break the one node whose job is the farewell."""
        assert moving_on_instructions(_Node(is_end=True)) is None

    def test_a_node_with_no_way_out_is_left_alone(self):
        """Telling a model to call one of no functions is worse than silence,
        and a dead end is a graph problem a prompt cannot fix."""
        assert moving_on_instructions(_Node(out_edges=())) is None

    def test_no_node_at_all(self):
        assert moving_on_instructions(None) is None


class TestTheAgentThatMayHangUp:
    def test_the_exception_is_named(self):
        """Otherwise the two instructions contradict each other, and the agent
        that may hang up is the one told most firmly not to."""
        text = moving_on_instructions(_Node(), agent_can_end_call=True)
        assert "end_call" in text
        assert "Do not say goodbye" in text

    def test_it_is_absent_when_the_agent_may_not(self):
        assert "end_call" not in moving_on_instructions(_Node())


class TestItStaysShort:
    def test_the_block_is_small(self):
        """It is prepended to every turn of every node, so it is paid for on
        every request and it pushes the operator's own words further from the
        model's attention."""
        assert len(moving_on_instructions(_Node(), agent_can_end_call=True)) < 500


class TestNeverClaimingWhatYouDidNotDo:
    """The rule that would have saved run 313's caller a wasted trip.

    He was told a reminder would be sent for an appointment that was never
    booked. The node holding the booking tool was never reached, and nothing
    in the prompt made the difference between doing a thing and saying it.
    """

    def test_every_node_gets_it(self):
        from api.services.workflow.step_instructions import (
            action_honesty_instructions,
        )

        text = action_honesty_instructions()
        assert "unless a tool has just" in text

    def test_it_names_the_actions_that_are_not_real_until_confirmed(self):
        from api.services.workflow.step_instructions import ACTION_HONESTY

        for action in ("booking", "cancellation", "payment", "message", "transfer"):
            assert action in ACTION_HONESTY

    def test_a_failed_tool_must_be_admitted(self):
        """The other half. An agent that hides a tool failure behind a
        reassurance is the same bug wearing a politer face."""
        from api.services.workflow.step_instructions import ACTION_HONESTY

        assert "failed" in ACTION_HONESTY
        assert "never cover it" in ACTION_HONESTY

    def test_it_reaches_the_composed_prompt_of_an_end_node(self):
        """Deliberately the end node: moving_on_instructions stays silent
        there, so this is where a mistake would show as the rule going
        missing exactly where a call gets wrapped up."""
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_system_prompt_for_node,
        )

        class _N:
            is_end = True
            out_edges: list = []
            prompt = "Say goodbye."
            add_global_prompt = False

        class _W:
            global_node_id = None
            nodes: dict = {}

        text = compose_system_prompt_for_node(
            node=_N(),
            workflow=_W(),
            format_prompt=lambda p: p,
            has_recordings=False,
            today_line="Today is Friday.",
        )
        assert "unless a tool has just" in text
        assert "cannot end the call" not in text
