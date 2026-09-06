"""One agent handing the call to another.

The whole design is that splicing happens once, at load, so the runtime never
learns a second execution path. That makes this module pure JSON in, JSON out
— and it makes these tests the only thing standing between a squad and a graph
the runtime cannot walk.
"""

import pytest

from api.services.workflow.squad import (
    HANDOFF,
    MAX_DEPTH,
    SquadError,
    assemble,
    handoff_targets,
    has_handoffs,
)


def _node(node_id, node_type, **data):
    return {"id": node_id, "type": node_type, "data": data}


def _edge(source, target):
    return {"id": f"{source}-{target}", "source": source, "target": target}


def _agent(prefix="m", *, global_prompt=None, with_qa=True, end=True):
    """A member agent shaped like one a creation path actually makes."""
    nodes = [
        _node(f"{prefix}-start", "startCall", greeting="Hello from the member"),
        _node(f"{prefix}-1", "agentNode", prompt="Take the payment"),
    ]
    edges = [_edge(f"{prefix}-start", f"{prefix}-1")]
    if global_prompt is not None:
        nodes.append(_node(f"{prefix}-global", "globalNode", prompt=global_prompt))
    if with_qa:
        nodes.append(_node(f"{prefix}-qa", "qa", qa_enabled=True))
    if end:
        nodes.append(_node(f"{prefix}-end", "endCall", prompt="Goodbye"))
        edges.append(_edge(f"{prefix}-1", f"{prefix}-end"))
    return {"nodes": nodes, "edges": edges}


def _parent(reference="member-uuid"):
    return {
        "nodes": [
            _node("start", "startCall", greeting="Hello"),
            _node("reception", "agentNode", prompt="Find out what they want"),
            _node("h", HANDOFF, agent_uuid=reference, name="Billing"),
            _node("global", "globalNode", prompt="We are Sunrise Dental"),
            _node("qa", "qa", qa_enabled=True),
        ],
        "edges": [_edge("start", "reception"), _edge("reception", "h")],
    }


def _by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def _types(graph):
    return [n["type"] for n in graph["nodes"]]


class TestDoingNothing:
    def test_a_workflow_with_no_handoff_is_returned_untouched(self):
        """The common case must cost one scan and no copying."""
        plain = _agent()
        assert assemble(plain, load_member=lambda _: None) is plain

    @pytest.mark.parametrize("raw", [None, "nonsense", 7, []])
    def test_something_that_is_not_a_workflow_is_passed_through(self, raw):
        assert assemble(raw, load_member=lambda _: None) is raw

    def test_finds_the_agents_a_workflow_hands_off_to(self):
        assert handoff_targets(_parent("billing")) == ["billing"]

    def test_an_unfinished_handoff_still_needs_assembling(self):
        """Anything deciding whether to skip assembly must ask this.

        `handoff_targets` is empty for a step with no agent chosen, so a caller
        gating on it would skip assembly and leave the node in the graph — the
        one case that must not reach the runtime.
        """
        assert handoff_targets(_parent("")) == []
        assert has_handoffs(_parent("")) is True

    def test_a_plain_workflow_needs_no_assembly(self):
        assert has_handoffs(_agent()) is False


class TestWhatGetsSpliced:
    def _assembled(self, **kwargs):
        member = _agent(**kwargs)
        return assemble(_parent(), load_member=lambda _: member)

    def test_the_handoff_node_is_gone(self):
        """It is scaffolding. The runtime has no idea what one is."""
        assert HANDOFF not in _types(self._assembled())

    def test_the_members_conversation_is_there(self):
        assert "h__m-1" in _by_id(self._assembled())

    def test_the_call_arrives_where_the_member_starts_talking(self):
        graph = self._assembled()
        arriving = [e["target"] for e in graph["edges"] if e["source"] == "reception"]
        assert arriving == ["h__m-1"]

    def test_the_members_own_edges_come_with_it(self):
        graph = self._assembled()
        assert {"source": "h__m-1", "target": "h__m-end"} in [
            {"source": e["source"], "target": e["target"]} for e in graph["edges"]
        ]

    def test_the_members_ending_still_ends_the_call(self):
        assert _by_id(self._assembled())["h__m-end"]["type"] == "endCall"


class TestWhatGetsDropped:
    def test_the_member_does_not_greet_them_again(self):
        """Being greeted twice is the clearest possible tell that this is
        software."""
        graph = assemble(_parent(), load_member=lambda _: _agent())
        assert _types(graph).count("startCall") == 1

    def test_there_is_still_only_one_global(self):
        """Two is not a thing the graph can hold."""
        graph = assemble(
            _parent(), load_member=lambda _: _agent(global_prompt="Be brief")
        )
        assert _types(graph).count("globalNode") == 1

    def test_the_call_is_not_reviewed_twice(self):
        """One call, one review — and one bill for it."""
        graph = assemble(_parent(), load_member=lambda _: _agent(with_qa=True))
        assert _types(graph).count("qa") == 1

    def test_no_edge_is_left_pointing_at_something_that_was_dropped(self):
        graph = assemble(
            _parent(), load_member=lambda _: _agent(global_prompt="Be brief")
        )
        ids = {n["id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            assert edge["source"] in ids, edge
            assert edge["target"] in ids, edge


class TestTheMembersGlobalPrompt:
    def test_is_folded_into_the_members_own_nodes(self):
        """Dropping it would silently change how an agent behaves the moment
        somebody reuses it."""
        graph = assemble(
            _parent(),
            load_member=lambda _: _agent(global_prompt="Never quote a price"),
        )
        assert "Never quote a price" in _by_id(graph)["h__m-1"]["data"]["prompt"]

    def test_keeps_the_members_own_node_prompt(self):
        graph = assemble(
            _parent(), load_member=lambda _: _agent(global_prompt="Never quote")
        )
        assert "Take the payment" in _by_id(graph)["h__m-1"]["data"]["prompt"]

    def test_the_parents_global_still_applies_inside_the_member(self):
        """It is where an account puts what is true of every call — who we are,
        what we may not say — and a second agent speaking does not change
        that."""
        graph = assemble(
            _parent(), load_member=lambda _: _agent(global_prompt="Never quote")
        )
        assert (
            _by_id(graph)["h__m-1"]["data"].get("add_global_prompt", True) is not False
        )

    def test_a_member_node_that_opted_out_stays_opted_out(self):
        member = _agent(global_prompt="Never quote")
        member["nodes"][1]["data"]["add_global_prompt"] = False
        graph = assemble(_parent(), load_member=lambda _: member)
        assert "Never quote" not in _by_id(graph)["h__m-1"]["data"]["prompt"]


class TestTheSameAgentTwice:
    def test_two_handoffs_to_one_agent_do_not_collide(self):
        """Without namespacing the second copy silently overwrites the first,
        and half the squad stops existing."""
        parent = _parent()
        parent["nodes"].append(_node("h2", HANDOFF, agent_uuid="member-uuid"))
        parent["edges"].append(_edge("reception", "h2"))
        graph = assemble(parent, load_member=lambda _: _agent())
        ids = {n["id"] for n in graph["nodes"]}
        assert {"h__m-1", "h2__m-1"} <= ids


class TestWhatIsRefused:
    def test_a_handoff_that_is_not_the_last_step(self):
        """A transfer does not come back, and pretending otherwise would drop
        the caller into a step nothing reaches."""
        parent = _parent()
        parent["nodes"].append(_node("after", "agentNode", prompt="And then?"))
        parent["edges"].append(_edge("h", "after"))
        with pytest.raises(SquadError, match="does not come back"):
            assemble(parent, load_member=lambda _: _agent())

    def test_a_handoff_with_no_agent_chosen(self):
        """Refused rather than skipped.

        The early exit used to be "are there agents to splice in", which a
        half-configured step answered no to — so the handoff node survived
        into the running graph, where the runtime has never heard of one and
        the caller walks into a dead end.
        """
        with pytest.raises(SquadError, match="no agent chosen"):
            assemble(_parent(""), load_member=lambda _: _agent())

    @pytest.mark.parametrize("reference", ["", "   ", None])
    def test_no_handoff_node_ever_survives_assembly(self, reference):
        parent = _parent()
        parent["nodes"][2]["data"]["agent_uuid"] = reference
        with pytest.raises(SquadError):
            assemble(parent, load_member=lambda _: _agent())

    def test_an_agent_that_no_longer_exists(self):
        """Silently dropping the step would lose a piece of the conversation
        with nothing to show for it."""
        with pytest.raises(SquadError, match="no longer exists"):
            assemble(_parent(), load_member=lambda _: None)

    def test_an_agent_belonging_to_another_account(self):
        """`load_member` is where the organization check lives, and returning
        None is how it says no."""
        with pytest.raises(SquadError, match="another account"):
            assemble(_parent(), load_member=lambda _: None)

    def test_a_member_with_no_starting_point(self):
        member = _agent()
        member["nodes"] = [n for n in member["nodes"] if n["type"] != "startCall"]
        with pytest.raises(SquadError, match="no starting point"):
            assemble(_parent(), load_member=lambda _: member)

    def test_two_agents_that_hand_off_to_each_other(self):
        """A circle is a call that never reaches a person."""

        def load(reference):
            member = _agent()
            member["nodes"].append(_node("m-h", HANDOFF, agent_uuid="member-uuid"))
            member["edges"].append(_edge("m-1", "m-h"))
            return member

        with pytest.raises(SquadError, match="circle"):
            assemble(_parent(), load_member=load)

    def test_a_chain_deeper_than_the_limit(self):
        """Every level multiplies the prompt the model is carrying."""
        depth = {"n": 0}

        def load(reference):
            depth["n"] += 1
            member = _agent(prefix=f"m{depth['n']}")
            member["nodes"].append(
                _node(f"m{depth['n']}-h", HANDOFF, agent_uuid=f"a{depth['n']}")
            )
            member["edges"].append(_edge(f"m{depth['n']}-1", f"m{depth['n']}-h"))
            return member

        with pytest.raises(SquadError, match=f"{MAX_DEPTH} deep"):
            assemble(_parent(), load_member=load)


class TestNesting:
    def test_a_members_own_handoff_is_flattened_too(self):
        calls = {"n": 0}

        def load(reference):
            calls["n"] += 1
            if calls["n"] == 1:
                member = _agent(prefix="mid", end=False)
                member["nodes"].append(_node("mid-h", HANDOFF, agent_uuid="deep"))
                member["edges"].append(_edge("mid-1", "mid-h"))
                return member
            return _agent(prefix="deep")

        graph = assemble(_parent(), load_member=load)
        assert HANDOFF not in _types(graph)
        ids = {n["id"] for n in graph["nodes"]}
        assert "h__mid-1" in ids
        assert "h__mid-h__deep-1" in ids
