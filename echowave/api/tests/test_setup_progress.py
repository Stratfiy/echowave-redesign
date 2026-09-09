"""The rail has to be right, or it is worse than no rail.

A new account meets a canvas and no sense of what "finished" means, and the two
places people actually stop — an agent nobody has called, an agent on no number
— look identical to a finished one from inside the editor.

Every step is a database fact rather than a wizard's memory of what somebody
clicked. These cover why that matters: a rail driven by clicks marks "tested"
for someone who opened the panel and closed it, stays marked after the agent is
rewritten, and knows nothing about the step a colleague did. A rail driven by
state cannot congratulate anyone for work that did not happen.
"""

from __future__ import annotations

from api.services.workflow.setup_progress import SetupProgress, Step, _steps


def _progress(**kwargs) -> SetupProgress:
    defaults = {"built": True, "tested": True, "live": True, "on_a_number": True}
    return SetupProgress(steps=_steps(**{**defaults, **kwargs}))


class TestTheShapeOfTheRail:
    def test_every_step_is_answerable_from_the_database(self):
        """Voice and knowledge are deliberately absent: neither has an
        incomplete state, and a step that is always done teaches people to stop
        reading the rail."""
        keys = [step.key for step in _progress().steps]
        assert keys == ["built", "tested", "live", "on_a_number"]

    def test_the_order_is_the_order_the_work_happens_in(self):
        keys = [step.key for step in _progress().steps]
        assert keys.index("built") < keys.index("tested") < keys.index("on_a_number")

    def test_every_step_tells_somebody_what_to_do(self):
        """A hint phrased as the missing state tells them off; phrased as the
        next action it tells them what to do."""
        for step in _progress().steps:
            assert step.hint and step.hint[0].isupper() and step.hint.endswith(".")

    def test_a_step_carries_a_key_that_is_never_shown(self):
        for step in _progress().steps:
            assert step.key and step.key.islower()


class TestCompleteness:
    def test_all_four_done_is_complete(self):
        assert _progress().complete is True

    def test_any_one_undone_is_not(self):
        for missing in ("built", "tested", "live", "on_a_number"):
            assert _progress(**{missing: False}).complete is False, missing


class TestTheNextStep:
    def test_it_is_the_first_undone_one(self):
        """First, not fewest-clicks: pointing somebody at a later step they
        cannot do yet is worse than pointing at nothing."""
        assert _progress(tested=False, on_a_number=False).next_step.key == "tested"

    def test_a_finished_agent_has_none(self):
        assert _progress().next_step is None

    def test_a_paused_agent_is_asked_to_be_switched_on(self):
        """The step that catches the agent that is otherwise ready. A paused
        agent does not answer however well it is configured, and nothing else
        on the rail would say so."""
        assert _progress(live=False).next_step.key == "live"

    def test_an_agent_on_a_number_but_never_tested_is_still_asked_to_test(self):
        """Order holds even when the later step was done first — somebody who
        attached a number before calling it has not tested it."""
        assert _progress(tested=False).next_step.key == "tested"


class TestAWorkflowThatIsNotOurs:
    def test_nothing_is_marked_done(self):
        """`for_workflow` returns this shape for a workflow the organization
        does not own, rather than raising: the caller is a panel on a page, and
        a rail that 500s is worse than one saying there is work to do."""
        missing = SetupProgress(
            steps=_steps(built=False, tested=False, live=False, on_a_number=False)
        )
        assert missing.complete is False
        assert missing.next_step.key == "built"
        assert all(isinstance(step, Step) and not step.done for step in missing.steps)
