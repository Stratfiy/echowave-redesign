"""The KYC state machine.

The property that matters most: only the carrier's approval permits calling.
Our own staff review is a pre-screen, and treating it as the gate would let an
account dial on a carrier sub-account that is still blocked — failures at best,
a licence problem at worst.
"""

import pytest

from api.enums import KycBusinessType, KycDocumentKind, KycStatus
from api.services.kyc.state import (
    KycTransitionError,
    assert_transition,
    can_transition,
    is_awaiting_carrier,
    is_awaiting_our_review,
    may_place_telephony_calls,
    missing_documents,
    required_documents,
)


class TestTelephonyGate:
    def test_only_carrier_approval_permits_calls(self):
        assert may_place_telephony_calls(KycStatus.CARRIER_APPROVED) is True

    @pytest.mark.parametrize(
        "status",
        [
            KycStatus.NOT_STARTED,
            KycStatus.SUBMITTED,
            KycStatus.UNDER_REVIEW,
            KycStatus.REJECTED,
            # The one that matters: we are satisfied, the carrier has not
            # decided, and the sub-account is still blocked on their side.
            KycStatus.FORWARDED,
            KycStatus.CARRIER_REJECTED,
        ],
    )
    def test_every_other_status_is_refused(self, status):
        assert may_place_telephony_calls(status) is False

    def test_our_own_approval_is_not_a_gate(self):
        """Explicit regression for the mistake this design exists to prevent.

        FORWARDED means staff signed off. It must not permit calls.
        """
        assert may_place_telephony_calls("forwarded") is False

    @pytest.mark.parametrize("status", [None, "", "approved", "nonsense", 42])
    def test_unknown_or_missing_status_fails_closed(self, status):
        assert may_place_telephony_calls(status) is False


class TestTransitions:
    @pytest.mark.parametrize(
        "current,target",
        [
            (KycStatus.NOT_STARTED, KycStatus.SUBMITTED),
            (KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW),
            (KycStatus.SUBMITTED, KycStatus.REJECTED),
            # Staff may forward without claiming it first.
            (KycStatus.SUBMITTED, KycStatus.FORWARDED),
            (KycStatus.UNDER_REVIEW, KycStatus.FORWARDED),
            (KycStatus.UNDER_REVIEW, KycStatus.REJECTED),
            (KycStatus.FORWARDED, KycStatus.CARRIER_APPROVED),
            (KycStatus.FORWARDED, KycStatus.CARRIER_REJECTED),
            # A rejection is recoverable — fix the documents and resubmit.
            (KycStatus.REJECTED, KycStatus.SUBMITTED),
            (KycStatus.CARRIER_REJECTED, KycStatus.SUBMITTED),
        ],
    )
    def test_legal_transitions(self, current, target):
        assert can_transition(current, target) is True

    @pytest.mark.parametrize(
        "current,target",
        [
            # Cannot skip our review and land on the carrier's verdict.
            (KycStatus.SUBMITTED, KycStatus.CARRIER_APPROVED),
            (KycStatus.NOT_STARTED, KycStatus.CARRIER_APPROVED),
            (KycStatus.NOT_STARTED, KycStatus.FORWARDED),
            # Cannot forward something nobody submitted.
            (KycStatus.NOT_STARTED, KycStatus.UNDER_REVIEW),
            # Approval is terminal; nothing here may undo it.
            (KycStatus.CARRIER_APPROVED, KycStatus.SUBMITTED),
            (KycStatus.CARRIER_APPROVED, KycStatus.REJECTED),
            (KycStatus.CARRIER_APPROVED, KycStatus.CARRIER_REJECTED),
            # A rejection is not a decision we can reverse in place.
            (KycStatus.REJECTED, KycStatus.FORWARDED),
            (KycStatus.REJECTED, KycStatus.CARRIER_APPROVED),
        ],
    )
    def test_illegal_transitions(self, current, target):
        assert can_transition(current, target) is False

    def test_assert_transition_raises_on_an_illegal_move(self):
        with pytest.raises(KycTransitionError) as excinfo:
            assert_transition(KycStatus.SUBMITTED, KycStatus.CARRIER_APPROVED)
        # The message names both ends, so a bad route is diagnosable from a log.
        assert "submitted" in str(excinfo.value)
        assert "carrier_approved" in str(excinfo.value)

    def test_assert_transition_returns_the_target_on_a_legal_move(self):
        assert (
            assert_transition(KycStatus.FORWARDED, KycStatus.CARRIER_APPROVED)
            is KycStatus.CARRIER_APPROVED
        )

    def test_a_garbage_status_is_never_transitionable(self):
        assert can_transition("banana", KycStatus.SUBMITTED) is False
        assert can_transition(KycStatus.SUBMITTED, "banana") is False


class TestQueuePredicates:
    @pytest.mark.parametrize("status", [KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW])
    def test_awaiting_our_review(self, status):
        assert is_awaiting_our_review(status) is True
        assert is_awaiting_carrier(status) is False

    def test_awaiting_carrier(self):
        assert is_awaiting_carrier(KycStatus.FORWARDED) is True
        assert is_awaiting_our_review(KycStatus.FORWARDED) is False

    @pytest.mark.parametrize(
        "status",
        [KycStatus.NOT_STARTED, KycStatus.CARRIER_APPROVED, KycStatus.REJECTED],
    )
    def test_settled_states_are_in_neither_queue(self, status):
        assert is_awaiting_our_review(status) is False
        assert is_awaiting_carrier(status) is False


class TestDocumentRequirements:
    def test_a_company_needs_incorporation_and_gst(self):
        assert required_documents(KycBusinessType.COMPANY) == {
            KycDocumentKind.CERTIFICATE_OF_INCORPORATION,
            KycDocumentKind.GST_CERTIFICATE,
        }

    def test_an_individual_needs_identity_and_address(self):
        assert required_documents(KycBusinessType.INDIVIDUAL) == {
            KycDocumentKind.AUTHORISED_SIGNATORY_ID,
            KycDocumentKind.ADDRESS_PROOF,
        }

    def test_an_unset_business_type_requires_nothing(self):
        """We cannot say what is missing until they tell us what they are."""
        assert required_documents(None) == frozenset()
        assert required_documents("partnership-firm") == frozenset()

    def test_missing_documents_reports_the_gap(self):
        assert missing_documents(
            KycBusinessType.COMPANY,
            {KycDocumentKind.CERTIFICATE_OF_INCORPORATION},
        ) == {KycDocumentKind.GST_CERTIFICATE}

    def test_nothing_missing_when_all_supplied(self):
        assert (
            missing_documents(
                KycBusinessType.COMPANY,
                {"certificate_of_incorporation", "gst_certificate"},
            )
            == frozenset()
        )

    def test_extra_documents_do_not_satisfy_a_requirement(self):
        """Uploading three address proofs is not a GST certificate."""
        assert missing_documents(
            KycBusinessType.COMPANY,
            {KycDocumentKind.ADDRESS_PROOF, KycDocumentKind.OTHER},
        ) == {
            KycDocumentKind.CERTIFICATE_OF_INCORPORATION,
            KycDocumentKind.GST_CERTIFICATE,
        }

    def test_an_unrecognised_supplied_kind_is_ignored_not_counted(self):
        assert missing_documents(
            KycBusinessType.COMPANY,
            {"gst_certificate", "made-up-document"},
        ) == {KycDocumentKind.CERTIFICATE_OF_INCORPORATION}
