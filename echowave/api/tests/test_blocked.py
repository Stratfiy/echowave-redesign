"""When a bot stops, the wall is named and there is a way past it.

``ask_for_decision`` already does this for a question the bot chose to ask.
This is the other half: the times something stopped it. Those rows rendered
as an amber icon and a sentence, and a sentence is not something a person
can act on.

The tests below are mostly about restraint. The failure modes here are
offering a door that is not there, guessing a wall from the wording of a
summary, and burying the reader in nine choices.
"""

from api.enums import AgentEventKind
from api.services.workflow import blocked


def wall(kind=AgentEventKind.COULD_NOT.value, payload=None, workflow_id=7):
    return blocked.classify(kind, payload or {}, workflow_id=workflow_id)


class TestWhichRowsAreWalls:
    def test_could_not_is(self):
        assert wall(AgentEventKind.COULD_NOT.value) is not None

    def test_needs_attention_is(self):
        assert wall(AgentEventKind.NEEDS_ATTENTION.value) is not None

    def test_an_ordinary_message_is_not(self):
        assert wall(AgentEventKind.MESSAGE.value) is None

    def test_a_question_the_bot_chose_to_ask_is_not(self):
        # needs_decision and needs_secret have their own cards. A second one
        # would be two things to press for one problem.
        assert wall(AgentEventKind.NEEDS_DECISION.value) is None
        assert wall(AgentEventKind.NEEDS_SECRET.value) is None


class TestTheWallsItNames:
    def test_out_of_credit_says_so_and_offers_a_top_up(self):
        found = wall(payload={"reason": "no_quota"})
        assert found.reason == "no_quota"
        assert "credit" in found.says
        # Every writer of this reason refuses the run before it starts.
        assert "part-way" not in found.says
        assert "did not start" in found.says
        assert any("/billing" == w.href for w in found.ways)

    def test_out_of_credit_does_not_read_as_broken(self):
        # The difference between "it broke" and "it ran out" is the one
        # thing the operator can fix in a minute.
        found = wall(payload={"reason": "no_quota"})
        assert "error" not in found.says.lower()

    def test_a_missing_field_is_named_rather_than_counted(self):
        found = wall(payload={"missing_fields": ["order_id", "phone"]})
        assert "order_id" in found.says and "phone" in found.says

    def test_many_missing_fields_are_summarised_rather_than_listed(self):
        found = wall(payload={"missing_fields": [f"f{i}" for i in range(20)]})
        assert "f0, f1, f2 and 17 more" in found.says
        assert "f9" not in found.says

    def test_an_empty_missing_list_is_not_treated_as_missing_fields(self):
        found = wall(payload={"missing_fields": []})
        assert found.reason != "missing_fields"

    def test_an_error_says_a_step_threw(self):
        found = wall(payload={"error": "boom"})
        assert found.reason == "failed"

    def test_a_row_with_nothing_to_go_on_still_gets_a_card(self):
        # Honest about not knowing, rather than guessing a wall. Still better
        # than a bare sentence: there is somewhere to look.
        found = wall(payload={})
        assert found.reason == "unknown"
        assert found.ways

    def test_nothing_is_guessed_from_the_summary_text(self):
        # A sentence written for a human is not a field. Matching on its
        # words breaks the first time somebody improves one.
        import inspect

        source = inspect.getsource(blocked)
        assert "summary" not in source.split('"""')[-1]


class TestTheWaysForward:
    def test_they_are_lettered_in_order(self):
        found = wall(payload={"reason": "no_quota"})
        assert [w.letter for w in found.ways] == ["A", "B", "C"]

    def test_there_are_never_more_than_three(self):
        # A wall with nine doors is a wall.
        for payload in (
            {"reason": "no_quota"},
            {"missing_fields": ["a"]},
            {"error": "x"},
            {},
        ):
            assert len(wall(payload=payload).ways) <= 3

    def test_there_is_always_at_least_one(self):
        for payload in ({"reason": "no_quota"}, {"missing_fields": ["a"]}, {}):
            assert wall(payload=payload).ways

    def test_every_way_points_inside_the_product(self):
        for payload in ({"reason": "no_quota"}, {"missing_fields": ["a"]}, {}):
            for way in wall(payload=payload).ways:
                assert way.href.startswith("/"), way.href

    def test_every_way_is_labelled_as_an_action(self):
        for way in wall(payload={"reason": "no_quota"}).ways:
            assert way.label and way.label[0].isupper()

    def test_a_row_with_no_bot_still_gets_somewhere_to_go(self):
        # A row can predate the workflow column or belong to the account
        # rather than one bot. It must not offer /workflow/None/runs.
        found = blocked.classify(
            AgentEventKind.COULD_NOT.value, {"error": "x"}, workflow_id=None
        )
        for way in found.ways:
            assert "None" not in way.href


class TestTheShapeTheScreenGets:
    def test_it_serialises_to_plain_data(self):
        found = wall(payload={"reason": "no_quota"}).as_dict()
        assert found["reason"] == "no_quota"
        assert found["says"]
        assert found["ways"][0]["letter"] == "A"
        assert found["ways"][0]["label"]
        assert found["ways"][0]["href"]
