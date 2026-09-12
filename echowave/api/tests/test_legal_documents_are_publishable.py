"""The click-wrap must point at something a customer can actually read.

``services/compliance/agreements.py`` blocks a campaign until the DPA is
accepted, records the version, the IP and the user agent, and never updates a
row. That machinery is careful and it is worth nothing if the URL in the
agreement is a 404 -- which it was: ``landing/`` is empty by design, so
``https://decibyl.ai/legal/dpa`` answered with a pointer to the app.

**A recorded acceptance of a document the customer could not read is worse than
no acceptance.** Enforceability under IT Act s10A wants reasonable notice, an
affirmative act, and terms accessible *before* acceptance. Only the middle one
was built, and the row in the database made it look like all three.

These tests bind the three things that have to agree and previously agreed by
coincidence: the agreement list, the publisher's document list, and the source
files on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api.services.compliance.agreements import AGREEMENTS  # noqa: E402
from scripts.publish_legal import (  # noqa: E402
    DOCUMENTS,
    SOURCE,
    UNRESOLVED,
    strip_internal,
    to_html,
    unresolved,
)

BY_AGREEMENT = {d.agreement: d for d in DOCUMENTS if d.agreement}


class TestEveryAgreementHasAPage:
    @pytest.mark.parametrize("agreement", [a for a in AGREEMENTS], ids=lambda a: a.key)
    def test_the_url_matches_a_document_the_publisher_renders(self, agreement):
        """The agreement's URL and the publisher's slug are written in two
        files and must name the same page."""
        document = BY_AGREEMENT.get(agreement.key)
        assert document, (
            f"'{agreement.key}' is presented for acceptance at {agreement.url} "
            "but scripts/publish_legal.py renders no page for it"
        )
        path = urlparse(agreement.url).path.strip("/")
        assert path == document.slug, (
            f"'{agreement.key}' points at /{path} and the publisher writes "
            f"landing/{document.slug}/ -- one of the two is wrong"
        )

    @pytest.mark.parametrize("document", DOCUMENTS, ids=lambda d: d.slug)
    def test_its_source_exists(self, document):
        assert (SOURCE / document.source).exists()

    def test_a_required_agreement_is_never_unpublishable_by_omission(self):
        """A required agreement gates a campaign. One with no page would block
        every customer with no way to unblock themselves."""
        for agreement in AGREEMENTS:
            if agreement.required:
                assert agreement.key in BY_AGREEMENT, (
                    f"'{agreement.key}' is required before a campaign can run "
                    "and has no publishable document"
                )


class TestNothingUnfinishedReachesACustomer:
    @pytest.mark.parametrize("document", DOCUMENTS, ids=lambda d: d.slug)
    def test_the_marker_is_detected_wherever_it_appears(self, document):
        """Not an assertion that the documents are finished -- several are not,
        and that is tracked. It asserts the DETECTOR works, so an unfinished
        document cannot be published by accident."""
        text = strip_internal((SOURCE / document.source).read_text())
        seeded = text + f"\n\nRetention is [{UNRESOLVED} — how long?]\n"
        assert unresolved(seeded), "the publisher would publish a placeholder"

    def test_the_internal_briefing_never_reaches_the_page(self):
        """DPA-TEMPLATE.md opens with a section addressed to whoever finishes
        the draft -- a table of what counsel must confirm. A customer being
        asked to agree to a contract must not be reading our notes to
        ourselves."""
        raw = (SOURCE / "DPA-TEMPLATE.md").read_text()
        assert "Data Processing Agreement — template" in raw
        assert "Data Processing Agreement — template" not in strip_internal(raw)

    def test_the_agreement_body_survives_the_strip(self):
        """The other direction, and the more dangerous one: a strip that ate
        the contract would produce a short, clean, publishable page with no
        obligations in it."""
        stripped = strip_internal((SOURCE / "DPA-TEMPLATE.md").read_text())
        for clause in (
            "Sub-processors",
            "Retention and deletion",
            "Personal Data Breach",
        ):
            assert clause in stripped, f"stripping removed '{clause}' from the DPA"


class TestTheRenderIsSafe:
    def test_angle_brackets_in_the_source_cannot_become_markup(self):
        """These documents quote vendor names, paths and email formats. One
        stray bracket becoming a tag inside a contract is a defect nobody would
        see until a clause rendered wrong."""
        rendered = to_html("A note about <script>alert(1)</script> and paths.")
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_tables_render_as_tables(self):
        """Annex B is a table of security measures. Losing its structure would
        turn the Art 32 annex into a wall of pipes."""
        rendered = to_html("| Measure | Status |\n|---|---|\n| Encryption | yes |")
        assert "<table>" in rendered and "<th>Measure</th>" in rendered
        assert "<td>Encryption</td>" in rendered

    def test_the_separator_row_is_not_rendered_as_content(self):
        assert "---" not in to_html("| A | B |\n|---|---|\n| 1 | 2 |")
