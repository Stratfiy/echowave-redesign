"""One mail a week, only to accounts that had calls, with the worst calls in it."""

from datetime import UTC, date, datetime

from api.tasks import weekly_digest


def test_the_week_is_last_monday_to_sunday():
    # Thursday 10 Sep 2026 → Mon 31 Aug to Sun 6 Sep.
    start, end = weekly_digest._week(datetime(2026, 9, 10, 4, tzinfo=UTC))
    assert (start, end) == (date(2026, 8, 31), date(2026, 9, 6))
    # Run on the Monday itself → the week that just ended.
    start, end = weekly_digest._week(datetime(2026, 9, 7, 4, tzinfo=UTC))
    assert (start, end) == (date(2026, 8, 31), date(2026, 9, 6))


def test_the_mail_reads_as_a_week_and_links_the_worst_calls():
    notice = weekly_digest.compose(
        account_name="Kritilabs",
        week_start=date(2026, 8, 31),
        week_end=date(2026, 9, 6),
        calls=142,
        minutes=388,
        charged_paise=312_000,
        worst=[
            {
                "run_id": 9,
                "workflow_id": 4,
                "agent_name": "Elock support",
                "score": 2,
                "summary": "Caller hung up mid-OTP.",
            },
        ],
        app_url="https://app.decibyl.ai/",
    )
    assert notice.subject == "Your Decibyl week: 142 calls, 3,120 credits"
    assert "142 calls · 388 minutes · 3,120 credits spent" in notice.body
    assert "2/10 — Elock support: Caller hung up mid-OTP." in notice.body
    assert "https://app.decibyl.ai/workflow/4/run/9" in notice.body
    assert notice.dedupe_key == "week:2026-08-31"
    assert notice.link == "/review"


def test_no_worst_calls_omits_the_section():
    notice = weekly_digest.compose(
        account_name="x",
        week_start=date(2026, 8, 31),
        week_end=date(2026, 9, 6),
        calls=3,
        minutes=5,
        charged_paise=1000,
        worst=[],
        app_url="",
    )
    assert "worth a listen" not in notice.body
