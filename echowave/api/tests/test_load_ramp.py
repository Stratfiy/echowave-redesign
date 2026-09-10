"""The pure arithmetic of the load-ramp harness.

The harness itself makes network calls and is run by an operator, not CI; but
its percentile, error-rate and knee math decide whether a run reports a false
"all clear", so that part is worth pinning.
"""

import pytest

from scripts.load_ramp import LevelResult, parse_levels


def _result(latencies, statuses):
    return LevelResult(
        concurrency=10, latencies_ms=list(latencies), statuses=list(statuses)
    )


class TestLevelResult:
    def test_ok_and_error_counts(self):
        r = _result([1, 2, 3], [200, 200, 500])
        assert r.ok == 2
        assert r.errors == 1
        assert r.error_rate == pytest.approx(1 / 3)

    def test_a_connection_error_counts_as_an_error(self):
        r = _result([1, 2], [200, "ConnectTimeout"])
        assert r.errors == 1
        assert r.error_rate == 0.5

    def test_percentiles(self):
        r = _result(range(1, 101), [200] * 100)
        assert r.pct(0.50) == 50
        assert r.pct(0.95) == 95
        assert r.pct(0.99) == 99

    def test_percentile_of_empty_is_zero_not_a_crash(self):
        assert _result([], []).pct(0.95) == 0.0

    def test_error_rate_of_empty_is_fully_failed(self):
        # No data must never read as "all healthy".
        assert _result([], []).error_rate == 1.0


class TestParseLevels:
    def test_parses_a_csv_ramp(self):
        assert parse_levels("1,10,25,50") == [1, 10, 25, 50]

    def test_rejects_zero_and_negatives(self):
        for bad in ("0,10", "-5,10", "abc"):
            with pytest.raises((ValueError,)):
                parse_levels(bad)
