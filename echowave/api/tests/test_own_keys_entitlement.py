"""Own keys are a commercial arrangement, switched on per account by staff.

A customer's own vendor key takes that slot off our rate card, so whether an
account may bring one is not a preference the customer sets. What is
defended here is the seam: the customer's preferences save carries whatever
the screen last read, and must never move the entitlement in either
direction.
"""

from api.schemas.organization_preferences import OrganizationPreferences
from api.services.organization_preferences import (
    STAFF_ONLY_FIELDS,
    with_staff_fields,
)


def test_the_entitlement_is_off_by_default():
    assert OrganizationPreferences().own_keys_allowed is False


def test_a_customer_save_cannot_switch_it_on():
    stored = OrganizationPreferences(own_keys_allowed=False, timezone="Asia/Kolkata")
    incoming = OrganizationPreferences(own_keys_allowed=True, timezone="Asia/Kolkata")
    assert with_staff_fields(incoming, stored).own_keys_allowed is False


def test_a_stale_form_cannot_switch_it_off():
    stored = OrganizationPreferences(own_keys_allowed=True)
    incoming = OrganizationPreferences(
        own_keys_allowed=False, test_phone_number="+911234567890"
    )
    merged = with_staff_fields(incoming, stored)
    assert merged.own_keys_allowed is True
    # The customer's own fields still land.
    assert merged.test_phone_number == "+911234567890"


def test_the_seam_names_every_staff_field():
    assert STAFF_ONLY_FIELDS == ("own_keys_allowed",)
