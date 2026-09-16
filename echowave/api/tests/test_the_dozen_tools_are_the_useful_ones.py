"""An app's dozen tools are chosen, not taken alphabetically.

`MAX_PER_APP` keeps twelve of an app's actions. The module said the
catalogue returned them "in the vendor's own order, most-used first" and
took the first dozen on that basis.

It does not. They come back alphabetically, and the first twelve of
Gmail's sixty -- verified against a real connected account -- were seven
ways to delete mail, three ways to create a label or filter, one batch
modify, and `FETCH_EMAILS`, which made it only because FETCH sorts
twelfth. No `SEND_EMAIL`. No reply. No search.

A founder who connected Gmail and asked his bot to check his email was
one alphabetical position away from that not working either.
"""

from api.services.integrations.composio import tool_sync

#: The real Gmail slugs, in the alphabetical order the vendor returns.
GMAIL = [
    "GMAIL_ADD_LABEL_TO_EMAIL",
    "GMAIL_BATCH_DELETE_MESSAGES",
    "GMAIL_BATCH_MODIFY_MESSAGES",
    "GMAIL_CREATE_EMAIL_DRAFT",
    "GMAIL_CREATE_FILTER",
    "GMAIL_CREATE_LABEL",
    "GMAIL_DELETE_DRAFT",
    "GMAIL_DELETE_FILTER",
    "GMAIL_DELETE_LABEL",
    "GMAIL_DELETE_MESSAGE",
    "GMAIL_DELETE_THREAD",
    "GMAIL_FETCH_EMAILS",
    "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
    "GMAIL_GET_ATTACHMENT",
    "GMAIL_GET_CONTACTS",
    "GMAIL_GET_PROFILE",
    "GMAIL_LIST_THREADS",
    "GMAIL_MOVE_TO_TRASH",
    "GMAIL_REPLY_TO_THREAD",
    "GMAIL_SEND_EMAIL",
]


def _chosen(slugs: list[str], limit: int = tool_sync.MAX_PER_APP) -> list[str]:
    actions = [{"slug": s} for s in slugs]
    return [a["slug"] for a in tool_sync.most_useful(actions, limit)]


class TestTheThingsPeopleActuallyAskFor:
    def test_sending_an_email_survives_the_cap(self):
        # The one that was missing. A bot that cannot send mail is not an
        # assistant, and no amount of asking differently would have fixed it.
        assert "GMAIL_SEND_EMAIL" in _chosen(GMAIL)

    def test_reading_email_survives_the_cap(self):
        assert "GMAIL_FETCH_EMAILS" in _chosen(GMAIL)

    def test_reads_come_before_writes(self):
        chosen = _chosen(GMAIL)
        first_write = next(
            i
            for i, s in enumerate(chosen)
            if tool_sync._verb(s) not in tool_sync._READ_VERBS
        )
        assert all(
            tool_sync._verb(s) in tool_sync._READ_VERBS for s in chosen[:first_write]
        )


class TestDestructiveActionsGoLast:
    def test_no_delete_displaces_a_read_or_a_send(self):
        chosen = _chosen(GMAIL)
        destructive = [
            s for s in chosen if tool_sync._verb(s) in tool_sync._DESTRUCTIVE_VERBS
        ]
        assert destructive == [], destructive

    def test_the_old_dozen_was_mostly_deletes(self):
        # Guards the test above from passing vacuously: this is what the
        # account actually had.
        old = GMAIL[: tool_sync.MAX_PER_APP]
        deletes = [s for s in old if tool_sync._verb(s) in tool_sync._DESTRUCTIVE_VERBS]
        assert len(deletes) >= 5
        assert "GMAIL_SEND_EMAIL" not in old

    def test_a_destructive_action_is_still_offered_when_there_is_room(self):
        # Last, not banned. An app with few actions should still expose them.
        few = ["GMAIL_DELETE_MESSAGE", "GMAIL_FETCH_EMAILS"]
        assert set(_chosen(few)) == set(few)
        assert _chosen(few)[0] == "GMAIL_FETCH_EMAILS"


class TestTheChoiceIsStable:
    def test_the_same_catalogue_gives_the_same_dozen(self):
        assert _chosen(GMAIL) == _chosen(GMAIL)

    def test_vendor_order_breaks_ties_inside_a_band(self):
        # Deterministic, so a re-sync does not churn somebody's tool list.
        slugs = ["APP_GET_A", "APP_GET_B", "APP_GET_C"]
        assert _chosen(slugs, 2) == ["APP_GET_A", "APP_GET_B"]

    def test_an_unknown_verb_sits_between_writes_and_deletes(self):
        slugs = ["APP_FROBNICATE_THING", "APP_DELETE_THING", "APP_SEND_THING"]
        assert _chosen(slugs, 3) == [
            "APP_SEND_THING",
            "APP_FROBNICATE_THING",
            "APP_DELETE_THING",
        ]

    def test_a_slug_with_no_verb_does_not_crash(self):
        assert _chosen(["APP", ""], 2) == ["APP", ""]


class TestTheRankingSeesTheWholeCatalogue:
    def test_more_is_fetched_than_is_kept(self):
        """The cap used to be the vendor's page size, so only twelve were
        ever fetched. A ranking cannot improve on a sample it never sees."""
        assert tool_sync.ACTIONS_CONSIDERED > tool_sync.MAX_PER_APP
        source = tool_sync.__file__
        with open(source) as fh:
            body = fh.read()
        assert "toolkit_actions(slug, limit=ACTIONS_CONSIDERED)" in body
