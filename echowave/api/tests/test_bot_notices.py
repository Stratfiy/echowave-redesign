"""Which of a bot's events ring the bell.

The inbox and the bell have existed for a while and everything in them is
about money or the account -- a low balance, an auto top-up, a number's
rent. Nothing in them is about what a bot did, which is what an owner
actually wants told while they are not looking at the screen.

The events already exist: ``agent_timeline`` writes one row per thing a bot
does, with a kind and the bot it belongs to. So the question is only which
of those rows also ring a bell, and these tests pin the three ways that
goes wrong: offering an event the runtime never emits, letting a
configuration blob opt into being told about everything, and a malformed
value quietly turning notifications off.
"""

from api.enums import AgentEventKind
from api.services.workflow import bot_notices


class TestWhatIsOffered:
    def test_every_offered_kind_is_one_the_runtime_emits(self):
        # An offered checkbox that can never fire is worse than no checkbox:
        # somebody ticks it and waits for a notice that cannot arrive.
        for notice in bot_notices.CATALOGUE:
            assert isinstance(notice.kind, AgentEventKind)

    def test_the_noisy_kinds_are_not_offered(self):
        # A notification is an interruption. Messages, call starts and credit
        # holds are right to keep on the timeline and wrong to be told about.
        for kind in (
            AgentEventKind.MESSAGE,
            AgentEventKind.CALL_STARTED,
            AgentEventKind.CALL_ANSWERED,
            AgentEventKind.CREDITS_HELD,
            AgentEventKind.ACTIVITY,
        ):
            assert kind.value not in bot_notices.NOTIFIABLE

    def test_every_notice_says_what_it_means(self):
        # A checkbox list of enum names is a list nobody can choose from.
        for notice in bot_notices.CATALOGUE:
            assert notice.label.strip()
            assert notice.when.strip()

    def test_the_defaults_are_the_ones_that_mean_something_is_wrong(self):
        assert bot_notices.DEFAULTS == {
            AgentEventKind.NEEDS_ATTENTION.value,
            AgentEventKind.COULD_NOT.value,
            AgentEventKind.ROUTINE_SKIPPED.value,
            AgentEventKind.NEEDS_SECRET.value,
        }

    def test_nothing_is_offered_twice(self):
        kinds = [n.kind.value for n in bot_notices.CATALOGUE]
        assert len(kinds) == len(set(kinds))


class TestWhatABotIsSetTo:
    def test_a_bot_nobody_configured_gets_the_defaults(self):
        assert bot_notices.selected_for({}) == bot_notices.DEFAULTS

    def test_a_missing_configuration_block_is_not_a_crash(self):
        assert bot_notices.selected_for(None) == bot_notices.DEFAULTS

    def test_a_chosen_list_is_obeyed(self):
        chosen = bot_notices.selected_for({"notify_on": ["escalated"]})
        assert chosen == {"escalated"}

    def test_choosing_nothing_is_a_decision_and_is_kept(self):
        # "Tell me nothing about this bot" is a legitimate answer, and is not
        # the same as never having chosen.
        assert bot_notices.selected_for({"notify_on": []}) == set()

    def test_an_unreadable_value_falls_back_to_the_defaults_not_to_silence(self):
        # Silence is the wrong failure: the point of the feature is being
        # told when a bot has stopped, and a malformed blob should not be
        # how somebody stops being told.
        assert bot_notices.selected_for({"notify_on": "escalated"}) == (
            bot_notices.DEFAULTS
        )
        assert bot_notices.selected_for({"notify_on": 7}) == bot_notices.DEFAULTS

    def test_a_kind_nobody_offers_cannot_be_smuggled_in(self):
        # Hand-editing the column must not opt a bot into a notice per
        # message it sends.
        chosen = bot_notices.selected_for({"notify_on": ["message", "escalated"]})
        assert chosen == {"escalated"}


class TestWhetherToRing:
    def test_it_rings_for_a_chosen_kind(self):
        assert bot_notices.wants({"notify_on": ["escalated"]}, "escalated")

    def test_it_does_not_ring_for_one_left_unchecked(self):
        assert not bot_notices.wants({"notify_on": ["escalated"]}, "could_not")

    def test_it_never_rings_for_a_kind_outside_the_catalogue(self):
        assert not bot_notices.wants({"notify_on": ["message"]}, "message")

    def test_an_unconfigured_bot_rings_for_the_defaults(self):
        assert bot_notices.wants({}, AgentEventKind.NEEDS_ATTENTION.value)
        assert not bot_notices.wants({}, AgentEventKind.DELIVERABLE.value)


class TestWhatIsStored:
    def test_it_is_written_in_catalogue_order(self):
        # A diff of two saved configurations should show what changed, not a
        # reshuffle.
        stored = bot_notices.store(["escalated", "needs_attention", "could_not"])
        assert stored == ["needs_attention", "could_not", "escalated"]

    def test_unknown_kinds_are_dropped_rather_than_stored(self):
        assert bot_notices.store(["escalated", "message"]) == ["escalated"]

    def test_choosing_nothing_stores_an_empty_list(self):
        assert bot_notices.store([]) == []

    def test_a_round_trip_is_stable(self):
        first = bot_notices.store(["deliverable", "escalated"])
        assert bot_notices.store(first) == first
