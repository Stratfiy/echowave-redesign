"""The per-call duration ceiling, enforced where the value is actually read.

`WorkflowConfigurations` already validates `max_call_duration` on write with
`le=MAX_CALL_DURATION_SECONDS`. This covers the clamp on **read**, and the
distance between those two moments is the reason it exists: the schema guards
the API that writes the row, this guards the JSON column that is read at dial
time. A row written before the constraint existed, restored from a backup,
edited directly in the database, or arriving through some later path that
does not go through the schema is otherwise honoured verbatim.

It matters more here than a bounds check usually would, because the number is
denominated in money. Every second bills telephony, transcription, a model and
speech synthesis at once, and the balance reservation holds
``min(estimate, balance)`` — so a call that outruns its estimate overdraws the
account rather than stopping. An unclamped value is not a long call. It is an
unbounded one.
"""

import pytest

from api.schemas.workflow_configurations import (
    DEFAULT_MAX_CALL_DURATION_SECONDS as DEFAULT,
)
from api.schemas.workflow_configurations import (
    MAX_CALL_DURATION_SECONDS as CEILING,
)
from api.services.pipecat.run_pipeline import _clamped_call_duration


class TestAValidValueIsHonoured:
    def test_a_duration_under_the_ceiling_is_used_as_given(self):
        assert _clamped_call_duration(600) == 600

    def test_the_ceiling_itself_is_allowed(self):
        """`le=`, not `lt=` — a customer who sets exactly the maximum gets it."""
        assert _clamped_call_duration(CEILING) == CEILING

    def test_a_numeric_string_is_accepted(self):
        """JSON columns are not type-safe; a value stored as a string is still
        a duration somebody meant."""
        assert _clamped_call_duration("900") == 900


class TestTheCeilingHolds:
    @pytest.mark.parametrize("stored", [CEILING + 1, 3600, 86_400, 10**9])
    def test_anything_above_the_ceiling_is_clamped_to_it(self, stored):
        """The case this function exists for. A day-long value is not a long
        call — it is an unbounded bill, on four vendors at once."""
        assert _clamped_call_duration(stored) == CEILING

    def test_the_clamp_never_raises_a_short_call(self):
        """Clamping is one-directional. A customer who chose 60 seconds meant
        60 seconds, and quietly extending it would spend their money."""
        assert _clamped_call_duration(60) == 60


class TestJunkCostsAShortCallNotAFailedOne:
    """A malformed configuration should degrade, not drop the call. The caller
    is already connected by the time this is read."""

    @pytest.mark.parametrize("junk", [None, "abc", "", [], {}, object()])
    def test_unparseable_values_fall_back_to_the_default(self, junk):
        assert _clamped_call_duration(junk) == DEFAULT

    @pytest.mark.parametrize("bad", [0, -1, -86_400])
    def test_non_positive_values_fall_back_to_the_default(self, bad):
        """Zero would end the call on the first heartbeat; negative is
        nonsense. Both are configuration errors, not instructions."""
        assert _clamped_call_duration(bad) == DEFAULT


def test_the_default_sits_below_the_ceiling():
    """If these ever cross, the fallback for junk would itself exceed the
    ceiling — the clamp would be handing out the maximum on every malformed
    row."""
    assert 0 < DEFAULT <= CEILING
