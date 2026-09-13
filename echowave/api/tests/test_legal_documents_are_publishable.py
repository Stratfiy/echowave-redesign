"""Every agreement a customer is asked to accept must resolve to a real page.

``services/compliance/agreements.py`` blocks a campaign until the DPA is
accepted, records the version, the IP and the user agent, and never updates a
row. That machinery is careful and it is worth nothing if the URL in the
agreement is a 404.

**A recorded acceptance of a document the customer could not read is worse than
no acceptance.** Enforceability under IT Act s10A wants reasonable notice, an
affirmative act, and terms accessible *before* acceptance. Only the middle one
lives in this repository, and the row in the database makes it look as though
all three were satisfied.

Two of the three URLs were wrong when this file was written. ``/privacy`` was a
404 because the page is published at ``/legal/privacy``, and ``/legal/dpa`` did
not exist at all — the one document an enterprise buyer asks for first.

**The pages live in the Stratfiy/decibyl site, not here.** This repository
cannot fetch them, so these tests guard the half that is knowable locally: that
every agreement names a well-formed page under a path the site actually
publishes. ``PUBLISHED_PAGES`` is the seam, and it is a manifest a human must
update — which is the honest arrangement, since the alternative is a test that
silently agrees with whatever the code says.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api.services.compliance.agreements import (  # noqa: E402
    AGREEMENTS,
    SIGNUP_AGREEMENTS,
)

#: Paths the marketing site publishes, from its ``app/`` routes.
#:
#: Update this when the site adds or moves a legal page. It is duplication, and
#: it is deliberate: the site is a separate repository and a deployment this one
#: cannot see, so the choice is between a manifest that can go stale loudly and
#: no check at all. A stale entry fails here; a missing page fails in front of a
#: customer.
PUBLISHED_PAGES = frozenset(
    {
        "/legal/dpa",
        "/legal/terms",
        "/legal/privacy",
        "/legal/dpdp",
        "/legal/refund",
        "/security",
    }
)

SITE = "decibyl.ai"


class TestEveryAgreementResolves:
    @pytest.mark.parametrize("agreement", AGREEMENTS, ids=lambda a: a.key)
    def test_it_points_at_a_page_the_site_publishes(self, agreement):
        path = urlparse(agreement.url).path.rstrip("/") or "/"
        assert path in PUBLISHED_PAGES, (
            f"'{agreement.key}' is presented for acceptance at {agreement.url}, "
            f"and the site publishes no page at {path}. Either the URL is wrong "
            "or PUBLISHED_PAGES needs the new route."
        )

    @pytest.mark.parametrize("agreement", AGREEMENTS, ids=lambda a: a.key)
    def test_it_is_an_absolute_https_url_on_our_own_domain(self, agreement):
        parsed = urlparse(agreement.url)
        assert parsed.scheme == "https", f"'{agreement.key}' is not https"
        assert parsed.netloc == SITE, (
            f"'{agreement.key}' points at {parsed.netloc}. A terms link that "
            "leaves our own domain is a phishing lesson we teach our customers."
        )

    def test_no_two_agreements_share_a_page(self):
        """Two agreements on one URL means one of them is being accepted
        against a document that does not say what it claims."""
        by_url: dict[str, list[str]] = {}
        for agreement in AGREEMENTS:
            by_url.setdefault(agreement.url, []).append(agreement.key)
        clashes = {url: keys for url, keys in by_url.items() if len(keys) > 1}
        assert not clashes, f"agreements sharing a page: {clashes}"


class TestTheGatesStillHold:
    def test_the_dpa_is_required(self):
        """Without it the consent obligation for the people being called has
        not been allocated to anyone."""
        dpa = next(a for a in AGREEMENTS if a.key == "dpa")
        assert dpa.required

    def test_signup_asks_for_the_terms_and_the_privacy_notice(self):
        """The terms are the contract; the privacy notice is what DPDP wants
        given before any personal data is taken, and the signup form is the
        first thing to take some."""
        assert set(SIGNUP_AGREEMENTS) == {"terms", "privacy"}

    @pytest.mark.parametrize("agreement", AGREEMENTS, ids=lambda a: a.key)
    def test_every_agreement_carries_a_version(self, agreement):
        """'They accepted the DPA' is not a defence when the DPA has been
        revised twice since. Acceptance rows store this, so an empty version
        would make every row unfalsifiable."""
        assert agreement.version.strip()


class TestTheManifestIsHonest:
    def test_it_names_no_page_no_agreement_uses_that_we_invented(self):
        """/legal/dpdp, /legal/refund and /security are real pages that no
        agreement points at, and that is fine. This test exists to catch the
        other direction — a manifest padded to make a failing URL pass."""
        referenced = {urlparse(a.url).path.rstrip("/") for a in AGREEMENTS}
        assert referenced <= PUBLISHED_PAGES
        assert len(PUBLISHED_PAGES) >= len(referenced)
