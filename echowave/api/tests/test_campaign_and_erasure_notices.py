"""Two notices that used to go nowhere: a campaign finishing, an erasure done."""

from api.services.campaign import notices as campaign_notices
from api.services.privacy import notices as privacy_notices


class TestCampaignCompleted:
    def test_it_counts_reached_and_not(self):
        notice = campaign_notices.completed(
            campaign_id=7,
            name="September renewals",
            total_rows=120,
            processed_rows=120,
            failed_rows=15,
            app_url="https://app.decibyl.ai/",
        )
        assert "September renewals" in notice.subject
        assert "105 of 120 calls connected, 15 did not." in notice.body
        assert "https://app.decibyl.ai/campaigns/7" in notice.body
        assert notice.link == "/campaigns/7"
        assert notice.dedupe_key == "completed:7"

    def test_no_failures_reads_cleanly(self):
        notice = campaign_notices.completed(
            campaign_id=1,
            name="x",
            total_rows=3,
            processed_rows=3,
            failed_rows=0,
            app_url="",
        )
        assert "3 of 3 calls connected." in notice.body


class TestErasureCompleted:
    def test_the_number_is_not_written_down_again(self):
        assert privacy_notices.mask_number("+91 98765 43210") == "a number ending 3210"
        assert privacy_notices.mask_number("12") == "the number"

    def test_it_says_what_was_erased_and_that_it_is_final(self):
        notice = privacy_notices.completed(
            request_id=9,
            number_hint="a number ending 3210",
            calls_erased=1,
            files_deleted=2,
        )
        assert "1 call and 2 recording or transcript files" in notice.body
        assert "cannot be undone" in notice.body
        assert notice.dedupe_key == "erasure:9"
        assert notice.link == "/privacy"
