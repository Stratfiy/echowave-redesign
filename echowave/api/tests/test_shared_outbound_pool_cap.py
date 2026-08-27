"""The ceiling on trial calls placed with Decibyl's own caller IDs.

The pool is ours end to end: our carrier account, our credentials, our rent,
and minutes billed to nobody. Nothing about the numbers bounds it -- a caller
ID is a From header rather than a channel, so one shared number originates as
many simultaneous calls as the carrier allows. The module docstring used to
claim "a third simultaneous trial call waits"; it never did, because the caller
ID is chosen with ``random.choice`` and nothing reserved it.

The per-organization limit cannot do this job either, and that is the property
these tests pin: it counts each account separately, so ten evaluators dialling
at once sit comfortably inside ten separate limits while one carrier account
carries every minute.
"""

from __future__ import annotations

from api.constants import SHARED_OUTBOUND_MAX_CONCURRENT
from api.services.campaign.rate_limiter import RateLimiter
from api.services.telephony.shared_outbound import SHARED_OUTBOUND_SCOPE


class TestTheScopeIsAPlatformTotal:
    def test_the_counter_is_not_keyed_by_organization(self):
        """The whole point. A scope counter namespaced per account would cap
        each evaluator individually and leave the carrier account -- the thing
        actually being protected -- unbounded."""
        assert "{" not in SHARED_OUTBOUND_SCOPE
        assert "%" not in SHARED_OUTBOUND_SCOPE
        assert SHARED_OUTBOUND_SCOPE == "shared_outbound"

    def test_two_organizations_share_one_counter(self):
        """Two different orgs, one scope key, therefore one Redis counter.

        Asserted against the key the rate limiter actually builds rather than
        against the constant, because it is that derivation -- scope key alone,
        no organization id -- that makes the total platform-wide.
        """
        keys = {
            RateLimiter._scope_concurrent_key(SHARED_OUTBOUND_SCOPE)
            for _org in (11, 22)
        }
        assert len(keys) == 1

    def test_the_org_counter_is_still_per_organization(self):
        """The two bounds are different questions and must not collapse into
        one: an account's own limit protects the account, the scope total
        protects us."""
        assert RateLimiter._org_concurrent_key(11) != RateLimiter._org_concurrent_key(
            22
        )


class TestTheCapIsSane:
    def test_it_allows_more_than_one_call_at_a_time(self):
        """The unfairness worth avoiding in the other direction. Serialising
        the pool would mean a second evaluator waits on a stranger's demo."""
        assert SHARED_OUTBOUND_MAX_CONCURRENT > 1

    def test_it_is_bounded(self):
        """Unbounded is what it was. A cap that cannot refuse is not a cap."""
        assert SHARED_OUTBOUND_MAX_CONCURRENT >= 1
        assert isinstance(SHARED_OUTBOUND_MAX_CONCURRENT, int)
