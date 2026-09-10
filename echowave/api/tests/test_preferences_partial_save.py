"""Saving one preference must not clear the ones the form never mentions.

A PUT here is a whole object, so a field the caller omits arrives carrying its
schema default and the save wrote that default over what was stored. The
Settings form sends two fields, so saving a timezone from that screen switched
``byok_fallback_to_managed`` off -- the flag that decides whether a call with no
usable customer key runs on Decibyl's key or is refused outright. It happened on
a live account while setting a timezone, and nothing said so.
"""

from api.schemas.organization_preferences import OrganizationPreferences
from api.services.organization_preferences import STAFF_ONLY_FIELDS, with_staff_fields


def _stored(**overrides) -> OrganizationPreferences:
    return OrganizationPreferences(
        **{
            "test_phone_number": "+911234567890",
            "timezone": "Asia/Kolkata",
            "own_keys_allowed": True,
            "byok_fallback_to_managed": True,
            **overrides,
        }
    )


class TestAFieldNobodyMentionedIsLeftAlone:
    def test_saving_a_timezone_keeps_the_fallback_flag(self):
        """The exact save that broke it."""
        incoming = OrganizationPreferences.model_validate(
            {"test_phone_number": "+911234567890", "timezone": "Europe/London"}
        )

        merged = with_staff_fields(incoming, _stored())

        assert merged.timezone == "Europe/London"
        assert merged.byok_fallback_to_managed is True

    def test_an_empty_save_changes_nothing(self):
        merged = with_staff_fields(
            OrganizationPreferences.model_validate({}), _stored()
        )

        assert merged.timezone == "Asia/Kolkata"
        assert merged.test_phone_number == "+911234567890"
        assert merged.byok_fallback_to_managed is True


class TestSentIsNotTheSameAsOmitted:
    def test_a_field_sent_as_false_is_honoured(self):
        """The whole point of reading model_fields_set: turning something off
        has to still work, or the fix trades one silent failure for another."""
        incoming = OrganizationPreferences.model_validate(
            {"byok_fallback_to_managed": False}
        )

        assert with_staff_fields(incoming, _stored()).byok_fallback_to_managed is False

    def test_a_field_sent_as_null_is_honoured(self):
        incoming = OrganizationPreferences.model_validate({"timezone": None})

        assert with_staff_fields(incoming, _stored()).timezone is None


class TestStaffFieldsStayStaff:
    def test_a_customer_cannot_grant_themselves_an_entitlement(self):
        incoming = OrganizationPreferences.model_validate({"own_keys_allowed": True})

        merged = with_staff_fields(incoming, _stored(own_keys_allowed=False))

        assert merged.own_keys_allowed is False

    def test_nor_take_one_away_by_omission(self):
        incoming = OrganizationPreferences.model_validate({"timezone": "UTC"})

        assert with_staff_fields(incoming, _stored()).own_keys_allowed is True

    def test_every_staff_field_is_covered_by_that_rule(self):
        """Fails if somebody adds a staff-only field and not its defence."""
        incoming = OrganizationPreferences.model_validate(
            {name: True for name in STAFF_ONLY_FIELDS}
        )
        stored = _stored(**{name: False for name in STAFF_ONLY_FIELDS})

        merged = with_staff_fields(incoming, stored)

        for name in STAFF_ONLY_FIELDS:
            assert getattr(merged, name) is False
