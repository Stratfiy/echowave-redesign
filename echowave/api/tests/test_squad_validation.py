"""Every way a squad is broken, found while somebody is still building it.

``assemble`` raises on the first problem, which is right for a call — there is
nothing useful to do with the second when the first has already stopped the
conversation — and wrong for an editor. Until this existed the only way to
discover any of the five was to place a call and have it fail, and the one
error that did surface arrived as a banner about the workflow with no node
attached, leaving the reader to find which step it meant.

The Elock squad is why this matters now: five workflows sharing one
authentication step, demoed to a customer. A handoff that resolves at design
time is the difference between a demo and an incident.
"""

from __future__ import annotations

import pytest

from api.services.workflow.squad import (
    CIRCULAR,
    MAX_DEPTH,
    MEMBER_MISSING,
    NO_AGENT_CHOSEN,
    NO_WAY_BACK,
    SquadError,
    assemble,
    validate,
)


def _handoff(node_id: str, agent: str | None = None) -> dict:
    data = {"agent_uuid": agent} if agent is not None else {}
    return {"id": node_id, "type": "handoff", "data": data}


def _agent(*nodes: dict, edges: list[dict] | None = None) -> dict:
    return {"nodes": list(nodes), "edges": edges or []}


def _start(node_id: str = "start") -> dict:
    return {"id": node_id, "type": "startCall", "data": {}}


class TestNothingToReport:
    def test_a_workflow_with_no_handoffs_is_silent(self):
        assert validate(_agent(_start()), load_member=lambda _: None) == []

    def test_a_non_dict_is_silent(self):
        assert validate(None, load_member=lambda _: None) == []
        assert validate("nonsense", load_member=lambda _: None) == []

    def test_a_healthy_squad_is_silent(self):
        member = _agent(_start("m-start"))
        squad = _agent(_start(), _handoff("h1", "member"))
        assert validate(squad, load_member={"member": member}.get) == []


class TestTheFiveConditions:
    def test_a_handoff_with_a_step_after_it(self):
        squad = _agent(
            _start(),
            _handoff("h1", "member"),
            edges=[{"source": "h1", "target": "start"}],
        )
        problems = validate(squad, load_member={"member": _agent(_start())}.get)
        assert [(p.node_id, p.code) for p in problems] == [("h1", "no_way_back")]
        assert problems[0].message == NO_WAY_BACK

    def test_a_handoff_with_no_agent_chosen(self):
        problems = validate(
            _agent(_start(), _handoff("h1")), load_member=lambda _: None
        )
        assert [(p.node_id, p.code) for p in problems] == [("h1", "no_agent_chosen")]
        assert problems[0].message == NO_AGENT_CHOSEN

    def test_an_empty_agent_reference_counts_as_unchosen(self):
        problems = validate(
            _agent(_start(), _handoff("h1", "   ")), load_member=lambda _: None
        )
        assert [p.code for p in problems] == ["no_agent_chosen"]

    def test_a_missing_or_cross_account_member(self):
        problems = validate(
            _agent(_start(), _handoff("h1", "gone")), load_member=lambda _: None
        )
        assert [(p.node_id, p.code) for p in problems] == [("h1", "member_missing")]
        assert problems[0].message == MEMBER_MISSING

    def test_a_circle(self):
        a = _agent(_start(), _handoff("h-a", "b"))
        b = _agent(_start("b-start"), _handoff("h-b", "a"))
        problems = validate(a, load_member={"a": a, "b": b}.get)
        assert [p.code for p in problems] == ["circular"]

    def test_a_chain_deeper_than_the_limit(self):
        depth = MAX_DEPTH + 2
        members = {
            str(level): _agent(
                _start(f"s{level}"), _handoff(f"h{level}", str(level + 1))
            )
            for level in range(depth)
        }
        problems = validate(members["0"], load_member=members.get)
        assert "too_deep" in {p.code for p in problems}


class TestItReportsEverything:
    """The point of the whole thing: an editor gets the full list."""

    def test_three_broken_handoffs_are_three_problems(self):
        squad = _agent(
            _start(),
            _handoff("h1"),
            _handoff("h2", "gone"),
            _handoff("h3", "member"),
            edges=[{"source": "h3", "target": "start"}],
        )
        problems = validate(squad, load_member={"member": _agent(_start())}.get)
        assert [(p.node_id, p.code) for p in problems] == [
            ("h1", "no_agent_chosen"),
            ("h2", "member_missing"),
            ("h3", "no_way_back"),
        ]

    def test_every_problem_names_its_node(self):
        squad = _agent(_start(), _handoff("h1"), _handoff("h2", "gone"))
        for problem in validate(squad, load_member=lambda _: None):
            assert problem.node_id is not None

    def test_a_problem_inside_a_member_is_reported(self):
        """A squad is only as sound as the agents it splices in."""
        member = _agent(_start("m"), _handoff("m-h1"))
        squad = _agent(_start(), _handoff("h1", "member"))
        problems = validate(squad, load_member={"member": member}.get)
        assert [(p.node_id, p.code) for p in problems] == [("m-h1", "no_agent_chosen")]


class TestItAgreesWithTheRuntime:
    """The sentence shown at design time is the sentence the runtime raises.

    These are single-sourced in squad.py for exactly this reason: an operator
    who fixes what the canvas told them and then reads a different wording in
    the log has been given two problems where there was one.
    """

    @pytest.mark.parametrize(
        "squad, load, expected",
        [
            (
                _agent(_start(), _handoff("h1")),
                {}.get,
                NO_AGENT_CHOSEN,
            ),
            (
                _agent(_start(), _handoff("h1", "gone")),
                {}.get,
                MEMBER_MISSING,
            ),
            (
                _agent(
                    _start(),
                    _handoff("h1", "member"),
                    edges=[{"source": "h1", "target": "start"}],
                ),
                {"member": _agent(_start())}.get,
                NO_WAY_BACK,
            ),
        ],
    )
    def test_the_same_words(self, squad, load, expected):
        problems = validate(squad, load_member=load)
        assert [p.message for p in problems] == [expected]

        with pytest.raises(SquadError) as raised:
            assemble(squad, load_member=load)
        assert str(raised.value) == expected

    def test_a_circle_raises_the_same_sentence(self):
        a = _agent(_start(), _handoff("h-a", "b"))
        b = _agent(_start("b-start"), _handoff("h-b", "a"))
        load = {"a": a, "b": b}.get
        assert [p.message for p in validate(a, load_member=load)] == [CIRCULAR]
        with pytest.raises(SquadError) as raised:
            assemble(a, load_member=load)
        assert str(raised.value) == CIRCULAR
