"""A bot with no version to run fails as a sentence, not a type error.

Three places reached into ``definition.workflow_json`` on something that can
be ``None``: the voice pipeline, the text runner and duplicate. Each gave
``AttributeError: 'NoneType' object has no attribute 'workflow_json'`` -- a
500 with a stack trace, mid-call, for a condition that is not a bad request
but a fact about the bot.

Reachable: ``create_workflow_run`` resolves draft, then
``released_definition_id``, then the ``is_current`` row, and binds ``None``
when all three miss. A publish that failed halfway gets there.
"""

import pytest

from api.services.workflow.definition_required import NoDefinition, require


class TestRequire:
    def test_a_real_definition_passes_straight_through(self):
        definition = object()
        assert require(definition, workflow_id=7) is definition

    def test_a_missing_one_raises_something_named(self):
        with pytest.raises(NoDefinition):
            require(None, workflow_id=7)

    def test_the_message_names_the_bot_so_somebody_knows_which_to_republish(self):
        with pytest.raises(NoDefinition) as caught:
            require(None, workflow_id=7, name="Clinic front desk")
        assert "Clinic front desk" in str(caught.value)

    def test_it_falls_back_to_the_id_when_there_is_no_name(self):
        with pytest.raises(NoDefinition) as caught:
            require(None, workflow_id=7)
        assert "7" in str(caught.value)

    def test_the_message_says_what_to_do_about_it(self):
        # An operator reading this in a log is asking "why did nobody answer".
        with pytest.raises(NoDefinition) as caught:
            require(None, workflow_id=7)
        assert "publish" in str(caught.value).lower()

    def test_it_carries_the_id_for_a_caller_that_wants_to_act_on_it(self):
        with pytest.raises(NoDefinition) as caught:
            require(None, workflow_id=7, name="X")
        assert caught.value.workflow_id == 7
        assert caught.value.name == "X"

    def test_a_falsy_but_real_definition_is_not_treated_as_missing(self):
        # Only None means absent. An empty graph is a different problem and
        # belongs to whoever validates graphs.
        class Empty:
            workflow_json: dict = {}

        empty = Empty()
        assert require(empty, workflow_id=7) is empty


class TestTheCallSitesUseIt:
    """Pinned by source, because the failure is the guard being removed.

    The three call sites are in a pipeline, a runner and a duplicate path;
    exercising each needs most of a live stack, and a test that mocked its
    way there would pass just as happily with the guard deleted.
    """

    def test_all_three_reach_for_it(self):
        import inspect

        from api.services.pipecat import run_pipeline
        from api.services.workflow import duplicate, text_chat_runner

        for module in (run_pipeline, text_chat_runner, duplicate):
            source = inspect.getsource(module)
            assert "definition_required.require(" in source, module.__name__
