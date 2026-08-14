"""Checks that run before an application is handed to the carrier.

Every rule here corresponds to something Plivo rejects for, and rejection is
expensive in a way that is easy to underestimate: the customer waits days for a
verdict, gets a message written for a compliance officer rather than for them,
and re-enters our review queue. Catching it at the point of submission costs a
form error.

The two rules below are the two most common causes of an Indian compliance
rejection. Both are invisible to the customer — a name that differs by a suffix
looks identical at a glance, and the same PDF in two slots looks like two
uploads on a screen that shows filenames.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationProblem:
    """One reason a submission would be rejected.

    ``field`` is what the UI should highlight; ``message`` is shown as written,
    so it is phrased for the customer rather than for a log.
    """

    field: str
    message: str


def normalize_for_comparison(value: str | None) -> str:
    """Collapse a name to what Plivo actually compares.

    Case, accents and runs of whitespace are folded away. Punctuation is
    **not**: "Acme Pvt Ltd" and "Acme Pvt. Ltd." differ in the eyes of a
    registrar, and treating them as equal here would let through the exact
    mismatch this check exists to catch.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def check_name_match(
    *, business_name: str | None, end_user_name: str | None
) -> ValidationProblem | None:
    """The registered name and the end user's name must be the same string.

    Plivo compares these character for character against the registration
    certificate. A trailing "Pvt Ltd" on one and not the other is the single
    most common rejection, and the rejection message says only that the
    documents do not match the application.
    """
    if not business_name or not end_user_name:
        return ValidationProblem(
            field="legal_name",
            message=(
                "Enter the business name exactly as it appears on your "
                "registration certificate."
            ),
        )

    if normalize_for_comparison(business_name) != normalize_for_comparison(
        end_user_name
    ):
        return ValidationProblem(
            field="legal_name",
            message=(
                f"The business name ({business_name!r}) and the authorised "
                f"signatory's registered entity name ({end_user_name!r}) have "
                "to match your registration certificate exactly — including "
                "suffixes like 'Pvt Ltd' and any punctuation. The carrier "
                "compares them character for character."
            ),
        )
    return None


def content_digest(content: bytes) -> str:
    """The hash stored on a document row, so duplicates are cheap to spot."""
    return hashlib.sha256(content).hexdigest()


def check_distinct_documents(documents) -> list[ValidationProblem]:
    """No single file may satisfy two document slots.

    A customer with one PDF containing several certificates will upload it
    everywhere, which reads as a complete set on our screen and is rejected by
    the carrier as a missing document. Compared by content hash rather than by
    filename, because the usual version of this is the same file uploaded twice
    under two names.

    Documents with no stored hash are skipped rather than assumed distinct —
    they predate the column, and refusing a submission over a hash we never
    recorded would block accounts that did nothing wrong.
    """
    by_digest: dict[str, list[str]] = {}
    for document in documents:
        digest = getattr(document, "content_sha256", None)
        if not digest:
            continue
        by_digest.setdefault(digest, []).append(getattr(document, "kind", "document"))

    problems = []
    for kinds in by_digest.values():
        if len(kinds) > 1:
            readable = " and ".join(sorted(k.replace("_", " ") for k in kinds))
            problems.append(
                ValidationProblem(
                    field="documents",
                    message=(
                        f"The same file is uploaded as both {readable}. The "
                        "carrier needs a separate document for each — upload "
                        "the specific certificate for each slot."
                    ),
                )
            )
    return problems


def check_address(record) -> list[ValidationProblem]:
    """The registered address Plivo's end_user requires.

    Checked here rather than at the point the customer types it, because an
    account can reach submission having filled the business details form before
    these fields existed.
    """
    problems = []
    if not (getattr(record, "address_line1", None) or "").strip():
        problems.append(
            ValidationProblem(
                field="address_line1",
                message="Enter the registered address on your certificate.",
            )
        )
    if not (getattr(record, "city", None) or "").strip():
        problems.append(ValidationProblem(field="city", message="Enter the city."))
    if not (getattr(record, "postal_code", None) or "").strip():
        problems.append(
            ValidationProblem(field="postal_code", message="Enter the PIN code.")
        )
    return problems


def validate_for_submission(record, *, end_user_name: str | None = None):
    """Every pre-submit problem on this record, in the order to show them.

    Returns a list so the customer sees all of it at once. Discovering three
    problems over three attempts, each with a wait in between, is how a signup
    is abandoned.

    ``end_user_name`` defaults to the record's own legal name, which is the
    normal case: the two are the same field until an operator overrides one.
    """
    problems: list[ValidationProblem] = []

    name_problem = check_name_match(
        business_name=record.legal_name,
        end_user_name=end_user_name if end_user_name is not None else record.legal_name,
    )
    if name_problem:
        problems.append(name_problem)

    problems.extend(check_address(record))
    problems.extend(check_distinct_documents(record.documents or []))
    return problems
