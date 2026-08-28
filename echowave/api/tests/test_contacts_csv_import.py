"""What a real spreadsheet does to a contact import.

The file is produced by a person, usually by exporting from something else, so
these are not edge cases — they are the common case. Every one of these was
chosen because getting it wrong produces the same silent outcome: contacts that
import successfully and then never match a caller, discovered weeks later when
somebody asks why the agent does not recognise anyone.
"""

from __future__ import annotations

from api.services.contacts import parse_contacts_csv


def _csv(*lines: str) -> bytes:
    return "\n".join(lines).encode()


class TestFindingTheColumns:
    def test_it_finds_the_phone_column_under_any_of_its_names(self):
        for header in ("phone", "Phone Number", "MOBILE", "msisdn", "contact number"):
            result = parse_contacts_csv(_csv(f"name,{header}", "Asha,+919876543210"))
            assert result.phone_column == header, header
            assert len(result.rows) == 1, header

    def test_a_file_with_no_phone_column_says_which_columns_it_found(self):
        """The operator has to fix this in their spreadsheet, so the message
        has to name what we saw rather than what we wanted."""
        result = parse_contacts_csv(_csv("name,email", "Asha,a@example.com"))

        assert not result.rows
        assert "email" in result.problems[0][1]

    def test_an_explicit_column_overrides_the_guess(self):
        result = parse_contacts_csv(
            _csv("phone,alt_phone", "+919876543210,+919999999999"),
            phone_column="alt_phone",
        )

        assert result.rows[0]["phone_normalized"] == "+919999999999"


class TestWhatExcelDoesToPhoneNumbers:
    def test_a_bom_does_not_hide_the_first_column(self):
        """Excel writes one on every CSV it saves as UTF-8. Without handling
        it the first header reads '\\ufeffphone' and nothing matches."""
        result = parse_contacts_csv("﻿phone\n+919876543210".encode())

        assert result.phone_column == "phone"
        assert len(result.rows) == 1

    def test_the_text_apostrophe_is_stripped(self):
        """How somebody stops Excel mangling a number is by prefixing it."""
        result = parse_contacts_csv(_csv("phone", "'+919876543210"))

        assert result.rows[0]["phone_normalized"] == "+919876543210"

    def test_a_number_stored_as_a_float_loses_its_trailing_zero_not_its_digits(self):
        result = parse_contacts_csv(_csv("phone", "919876543210.0"))

        assert result.rows[0]["phone_normalized"] == "+919876543210"

    def test_a_local_number_needs_the_country_to_be_usable(self):
        """The whole feature turns on this. A domestic export is full of
        '09876543210', and without the hint it normalizes to something no
        carrier will ever send."""
        with_hint = parse_contacts_csv(_csv("phone", "09876543210"), country_hint="IN")

        assert with_hint.rows[0]["phone_normalized"] == "+919876543210"


class TestWhatItRefuses:
    def test_text_in_the_phone_column_is_reported_not_stored(self):
        """The normalizer never raises — it calls anything it cannot read a
        'sip_extension'. Storing that would be a contact whose phone number is
        a sentence, matching nobody, counted as imported."""
        result = parse_contacts_csv(_csv("phone", "call him back", "+919876543210"))

        assert len(result.rows) == 1
        assert result.skipped == 1
        assert "call him back" in result.problems[0][1]

    def test_a_digit_only_extension_is_still_allowed(self):
        """A SIP deployment legitimately has these, and refusing them would
        make the import unusable for an ARI account."""
        result = parse_contacts_csv(_csv("phone", "1042"))

        assert result.rows[0]["phone_normalized"] == "1042"

    def test_the_same_number_twice_in_one_file_is_skipped(self):
        """Not politeness — two rows with the same conflict key in a single
        upsert is a statement Postgres refuses outright."""
        result = parse_contacts_csv(
            _csv("phone", "+919876543210", "+91 98765 43210"),
        )

        assert len(result.rows) == 1
        assert result.skipped == 1

    def test_a_trailing_blank_line_is_not_reported_as_a_problem(self):
        """Every file ends with one. Counting it would tell the operator their
        clean file had an error in it."""
        result = parse_contacts_csv(_csv("phone", "+919876543210", ""))

        assert len(result.rows) == 1
        assert result.skipped == 0

    def test_a_blank_phone_among_real_data_is_reported(self):
        result = parse_contacts_csv(_csv("name,phone", "Asha,", "Ravi,+919876543210"))

        assert len(result.rows) == 1
        assert result.skipped == 1

    def test_the_problem_list_is_capped(self):
        """A wholly broken file should say so once, not 50,000 times."""
        rows = ["phone"] + ["nonsense"] * 200
        result = parse_contacts_csv(_csv(*rows))

        assert result.skipped == 200
        assert len(result.problems) <= 25


class TestWhatReachesTheAgent:
    def test_every_other_column_is_kept_verbatim(self):
        """Guessing which of an account's columns matter would be guessing
        about their business. The agent's prompt decides."""
        result = parse_contacts_csv(
            _csv(
                "name,phone,policy_number,renewal_date",
                "Asha Rao,+919876543210,POL-1,2026-09-01",
            )
        )

        assert result.rows[0]["name"] == "Asha Rao"
        assert result.rows[0]["attributes"] == {
            "policy_number": "POL-1",
            "renewal_date": "2026-09-01",
        }

    def test_empty_cells_do_not_become_empty_attributes(self):
        """An attribute present but blank reads to a prompt as "we know this
        and it is nothing", which is not what a gap in a spreadsheet means."""
        result = parse_contacts_csv(
            _csv("name,phone,policy_number", "Asha,+919876543210,")
        )

        assert "policy_number" not in result.rows[0]["attributes"]

    def test_the_raw_number_is_kept_alongside_the_canonical_one(self):
        """So a list reads back the way it was uploaded rather than in our
        canonical form — the operator recognises their own data."""
        result = parse_contacts_csv(_csv("phone", "098765 43210"), country_hint="IN")

        assert result.rows[0]["phone_raw"] == "098765 43210"
        assert result.rows[0]["phone_normalized"] == "+919876543210"
