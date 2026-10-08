"""Browsers that still say "Asia/Calcutta" can finish signup.

Chrome on many Windows and Android installs reports India's zone by its old
name, and its own timezone list uses the old names too. The image's system
database dropped those aliases, so signup's timezone step refused every
choice that browser could offer. The ``tzdata`` package carries them.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from api.services.shell.onboarding import valid_timezone


@pytest.mark.parametrize(
    "legacy, canonical",
    [
        ("Asia/Calcutta", "Asia/Kolkata"),
        ("Asia/Katmandu", "Asia/Kathmandu"),
        ("Asia/Saigon", "Asia/Ho_Chi_Minh"),
        ("Europe/Kiev", "Europe/Kyiv"),
    ],
)
def test_legacy_name_is_accepted_and_means_the_same_zone(legacy, canonical):
    assert valid_timezone(legacy)
    moment = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    assert (
        moment.astimezone(ZoneInfo(legacy)).utcoffset()
        == moment.astimezone(ZoneInfo(canonical)).utcoffset()
    )


def test_a_made_up_zone_is_still_refused():
    assert not valid_timezone("Asia/Nowhere")
