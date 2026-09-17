"""A bot that runs on a schedule is not a bot that answers the phone.

Bot 31 was built from "every weekday at 8am, read Gmail and summarise it".
It came out with a greeting -- "Hi, what can I help you with?" -- a start
node told to "open briefly and find out what they need", an agent node
headed "## What this call is for" and "This is an inbound call: they rang
you", and an end node told to "summarise what was agreed and close
politely". Its persona said: follow the caller's language, ask one
question per turn, keep turns short because callers interrupt.

There is no caller. There is a cron tick.

So its routine fired and the bot did what it was built to do: it greeted,
and it asked what was needed, and nothing answered. Three fixes had by
then given it Gmail's tools, given it a schedule, and told it in a user
message that nobody was there -- and the persona node opens "these rules
apply to every turn of this conversation, without exception, and override
any other instruction that conflicts with them", so the message lost.

The generator only knew how to write call bots. A brief that names a
schedule is a task, not a conversation, and gets a graph shaped like one.
"""

from __future__ import annotations

from api.services.workflow import task_graph

SPEC = (
    "Every weekday at 8am, read Gmail from the last 24 hours and write one "
    "short summary: who needs a reply, anything with a deadline, anything "
    "about money. Nothing else."
)


class TestWhenATaskGraphIsUsedAtAll:
    def test_a_brief_naming_a_schedule_is_a_task(self):
        assert task_graph.wanted(SPEC) is True

    def test_a_brief_naming_no_schedule_is_not(self):
        """A WhatsApp support bot is a conversation, whatever its channel.
        Only the clock makes it a task."""
        assert task_graph.wanted("Answer questions about our opening hours") is False

    def test_empty_is_not(self):
        assert task_graph.wanted("") is False


class TestTheShapeOfIt:
    def _graph(self):
        return task_graph.build(name="Inbox Brief", spec=SPEC)

    def test_there_is_no_greeting(self):
        """Nobody is there to greet."""
        for node in self._graph()["nodes"]:
            assert not (node.get("data") or {}).get("greeting")

    def test_the_work_is_on_the_start_node(self):
        """The routine runner's first turn is the start node, and its output
        is thrown away as a greeting. A task bot's start node is the task, so
        the run's real turn is the one that matters."""
        start = [n for n in self._graph()["nodes"] if n["type"] == "startCall"]
        assert len(start) == 1
        assert SPEC[:40] in (start[0]["data"] or {}).get("prompt", "")

    def test_the_spec_is_the_task_not_a_conversation_topic(self):
        graph = self._graph()
        blob = str(graph)
        assert "How the conversation should go" not in blob
        assert "This is an inbound call" not in blob
        assert "What this call is for" not in blob

    def test_nothing_tells_it_to_ask_one_question_per_turn(self):
        """The rule that made it ask, when there was nobody to ask."""
        assert "one question per turn" not in str(self._graph())

    def test_nothing_tells_it_to_behave_as_if_it_had_a_caller(self):
        """`untrusted.GUARDRAIL` names "a caller's message" as one source of
        text not to obey, and that rule is shared safety wording worth more
        than the word costs -- so this checks the instructions, not the
        vocabulary."""
        blob = str(self._graph()).lower()
        for caller_shaped in (
            "follow the caller's language",
            "callers interrupt",
            "find out what they need",
            "hand off",
            "read back phone numbers",
        ):
            assert caller_shaped not in blob

    def test_it_is_told_not_to_ask(self):
        assert "do not ask" in str(self._graph()).lower()

    def test_it_is_told_to_report_what_it_found(self):
        assert "report" in str(self._graph()).lower()

    def test_it_is_told_to_say_what_stopped_it(self):
        """A run that produced nothing and a run that never happened look
        identical from outside."""
        assert "stopped" in str(self._graph()).lower()


class TestTheRulesItKeeps:
    def test_it_still_refuses_to_invent(self):
        assert "invent" in str(task_graph.build(name="x", spec=SPEC)).lower()

    def test_it_still_treats_what_it_reads_as_information(self):
        """The one guardrail that matters more here, not less: an unattended
        bot reading a stranger's email has nobody to sanity-check it."""
        from api.services.workflow import untrusted

        assert untrusted.GUARDRAIL in str(task_graph.build(name="x", spec=SPEC))

    def test_it_still_never_repeats_a_one_time_code(self):
        assert "otp" in str(task_graph.build(name="x", spec=SPEC)).lower()


class TestItIsAGraphTheRuntimeCanRun:
    def test_it_has_the_nodes_a_run_needs(self):
        graph = task_graph.build(name="Inbox Brief", spec=SPEC)
        types = [n["type"] for n in graph["nodes"]]
        assert "startCall" in types
        assert "endCall" in types

    def test_every_edge_points_at_a_node_that_exists(self):
        graph = task_graph.build(name="Inbox Brief", spec=SPEC)
        ids = {n["id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            assert edge["source"] in ids
            assert edge["target"] in ids

    def test_every_node_has_a_position(self):
        """The canvas renders these; a node with no position lands at 0,0 on
        top of another one."""
        for node in task_graph.build(name="x", spec=SPEC)["nodes"]:
            assert "position" in node

    def test_the_name_reaches_the_bot(self):
        assert "Inbox Brief" in str(task_graph.build(name="Inbox Brief", spec=SPEC))
