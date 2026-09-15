"""Phone numbers never reach a log handler in full."""

from __future__ import annotations

from api.utils.phone_masking import mask_phone_numbers, redact_record


class TestMasking:
    def test_an_indian_mobile_with_country_code(self):
        assert mask_phone_numbers(
            "Selected phone number +919876543210 for outbound call"
        ) == ("Selected phone number …3210 for outbound call")

    def test_a_ten_digit_number_and_a_spaced_one(self):
        assert mask_phone_numbers("caller 9876543210") == "caller …3210"
        assert mask_phone_numbers("to 98765 43210 now") == "to …3210 now"

    def test_short_ids_and_uuids_pass_through(self):
        line = "run 123456 doc 8f0245d2-6673-1da6 sid CA1234567 at 10:04:17.626"
        assert mask_phone_numbers(line) == line

    def test_the_patcher_rewrites_the_record(self):
        record = {"message": "Missed-call callback refused for +919876543210: busy"}
        redact_record(record)
        assert record["message"] == "Missed-call callback refused for …3210: busy"

    def test_the_patcher_never_raises(self):
        redact_record({})  # no message key: nothing happens, nothing thrown


class TestNoLogLineCarriesANumberOrToken:
    """The plan's check, as a test: no logger call interpolates a phone
    number, transcript, key or token unmasked."""

    NAMES = r"\{(to|sender|number|caller|phone_number|from_number|to_number|target_number|transcript|api_key|token|session_token|secret|password)(\[[^\]]*\])?\}"

    def test_grep_finds_nothing_unmasked(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if "logger." not in line:
                    continue
                if (
                    re.search(self.NAMES, line)
                    and "last_four(" not in line
                    and "short_token(" not in line
                ):
                    offenders.append(f"{path.relative_to(root)}:{lineno}")
        assert offenders == [], offenders
