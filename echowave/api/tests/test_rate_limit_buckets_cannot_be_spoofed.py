"""A caller must not be able to hand itself a fresh rate-limit bucket.

``_identity`` chose the bucket from two request headers and trusted both.

``x-api-key`` won outright when present, and the middleware runs long before
anything authenticates it, so the key did not have to be real. A different
random string per request meant a different bucket per request, and the limit
counted to one forever. That is not a weakened rate limiter, it is none:
``/user/auth/*`` is classified ``auth`` -- the brute-force bucket -- and one
extra header turned its twenty-a-minute into unlimited password guessing.

``x-forwarded-for`` had the same shape one step down. The leftmost entry was
taken as the client, and the leftmost entry is whatever the client typed. A
caller with no API key got the same unlimited allowance by varying an IP.

The fix for each is the same idea: count against something the caller does not
choose.

* The client address is read from the *right* of the forwarded chain, counting
  in by the number of proxies actually in front of us. Each proxy appends the
  peer it saw, so entries at that depth were written by our own infrastructure;
  everything to the left of them is caller-supplied text.
* The API key no longer replaces the address bucket, it adds one. A request is
  always counted against its address, and a request carrying a key is counted
  against the key as well. Rotating fake keys now buys nothing, because the
  address bucket is still counting.
"""

from __future__ import annotations

import pytest
from starlette.datastructures import Headers

from api import middleware_rate_limit as mw

CLIENT = ("203.0.113.9", 51111)


def _headers(**kwargs) -> Headers:
    return Headers({k.replace("_", "-"): v for k, v in kwargs.items()})


def _scope(client=CLIENT) -> dict:
    return {"type": "http", "client": client, "path": "/api/v1/things"}


class TestTheAddressCannotBeChosenByTheCaller:
    def test_a_forged_leftmost_entry_is_ignored(self, monkeypatch):
        """The regression. nginx appends the peer it saw, so with one proxy in
        front the rightmost entry is ours and everything left of it is text the
        caller sent."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        headers = _headers(x_forwarded_for="1.2.3.4, 203.0.113.9")
        assert mw._client_address(headers, _scope()) == "203.0.113.9"

    def test_varying_the_forged_part_does_not_change_the_bucket(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        first = mw._client_address(
            _headers(x_forwarded_for="1.1.1.1, 203.0.113.9"), _scope()
        )
        second = mw._client_address(
            _headers(x_forwarded_for="9.9.9.9, 203.0.113.9"), _scope()
        )
        third = mw._client_address(
            _headers(x_forwarded_for="a, b, c, d, 203.0.113.9"), _scope()
        )
        assert first == second == third == "203.0.113.9"

    def test_two_proxies_counts_in_two(self, monkeypatch):
        """Cloudflare in front of nginx: CF appends the client, nginx appends
        CF. The client is then the second from the right."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 2)
        headers = _headers(x_forwarded_for="1.2.3.4, 203.0.113.9, 198.51.100.7")
        assert mw._client_address(headers, _scope()) == "203.0.113.9"

    def test_with_no_proxy_in_front_the_header_is_ignored_entirely(self, monkeypatch):
        """A deployment reachable directly must not read the header at all:
        there is no proxy to have written any part of it."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 0)
        headers = _headers(x_forwarded_for="1.2.3.4, 5.6.7.8")
        assert mw._client_address(headers, _scope()) == "203.0.113.9"

    def test_a_chain_shorter_than_the_configured_hops_falls_back_to_the_socket(
        self, monkeypatch
    ):
        """Fewer entries than proxies means the header did not come through our
        chain. Indexing into it anyway would read caller text as the client.
        """
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 2)
        headers = _headers(x_forwarded_for="1.2.3.4")
        assert mw._client_address(headers, _scope()) == "203.0.113.9"

    def test_a_missing_header_falls_back_to_the_socket(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        assert mw._client_address(_headers(), _scope()) == "203.0.113.9"

    def test_no_socket_and_no_header_is_one_shared_bucket_not_a_crash(
        self, monkeypatch
    ):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        assert mw._client_address(_headers(), _scope(client=None)) == "unknown"

    def test_empty_entries_do_not_shift_the_index(self, monkeypatch):
        """ "1.2.3.4, , 203.0.113.9" has a blank the caller can insert to push
        the real entry out of position."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        headers = _headers(x_forwarded_for="1.2.3.4, , 203.0.113.9")
        assert mw._client_address(headers, _scope()) == "203.0.113.9"


class TestTheApiKeyAddsABucketRatherThanReplacingOne:
    def test_a_request_is_always_counted_against_its_address(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        identities = mw._identities(_headers(x_api_key="dcb_anything_at_all"), _scope())
        assert "ip:203.0.113.9" in identities

    def test_rotating_a_fake_key_does_not_escape_the_address_bucket(self, monkeypatch):
        """The regression: each new string used to be a brand new bucket."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        first = mw._identities(_headers(x_api_key="dcb_aaaaaaaa"), _scope())
        second = mw._identities(_headers(x_api_key="dcb_bbbbbbbb"), _scope())

        shared = set(first) & set(second)
        assert shared == {"ip:203.0.113.9"}, (
            "two requests from one address shared no bucket, so the limit "
            "counts each forged key separately and never fires"
        )

    def test_a_key_still_gets_its_own_bucket(self, monkeypatch):
        """The reason keys were used at all: one tenant must not be able to
        spend another's allowance."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        identities = mw._identities(_headers(x_api_key="dcb_aaaaaaaa"), _scope())
        assert len(identities) == 2
        assert any(i.startswith("key:") for i in identities)

    def test_the_key_bucket_does_not_contain_the_key(self, monkeypatch):
        """The bucket name is logged on every refusal and lives in a Redis
        keyspace that gets dumped. A prefix of a credential is still part of a
        credential, so the identity is a digest rather than a fragment."""
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        secret = "dcb_S3cr3tKeyMaterialThatMustNotLeak"
        identities = mw._identities(_headers(x_api_key=secret), _scope())

        joined = " ".join(identities)
        assert secret not in joined
        for length in range(6, len(secret)):
            assert secret[:length] not in joined

    def test_two_requests_on_one_key_share_the_key_bucket(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        headers = _headers(x_api_key="dcb_aaaaaaaa")
        from_one = mw._identities(headers, _scope(client=("198.51.100.1", 1)))
        from_two = mw._identities(headers, _scope(client=("198.51.100.2", 2)))

        key_buckets = {i for i in from_one if i.startswith("key:")}
        assert key_buckets == {i for i in from_two if i.startswith("key:")}

    def test_a_request_with_no_key_has_only_the_address_bucket(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        assert mw._identities(_headers(), _scope()) == ["ip:203.0.113.9"]

    def test_a_blank_key_header_is_not_a_bucket(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
        assert mw._identities(_headers(x_api_key="   "), _scope()) == ["ip:203.0.113.9"]


@pytest.mark.asyncio
class TestEveryBucketIsActuallyChecked:
    """Computing both and checking one would be the same bug again."""

    async def test_exceeding_either_bucket_refuses_the_request(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", True)
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)

        for over in ("ip:203.0.113.9", "key:"):
            checked: list[str] = []
            served = False

            async def _check(*, bucket, identity, limit, window_secs):
                checked.append(identity)
                return (not identity.startswith(over)), 30

            monkeypatch.setattr(mw.rate_limiter, "check", _check)

            async def _app(scope, receive, send):
                nonlocal served
                served = True

            sent: list[dict] = []

            async def _send(message):
                sent.append(message)

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/things",
                "client": CLIENT,
                "headers": [(b"x-api-key", b"dcb_aaaaaaaa")],
            }
            await mw.RateLimitMiddleware(_app)(scope, None, _send)

            assert served is False, f"a request over its {over} bucket was served"
            assert sent[0]["status"] == 429

    async def test_a_request_under_every_bucket_is_served(self, monkeypatch):
        monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", True)
        monkeypatch.setattr(mw, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)

        checked: list[str] = []

        async def _check(*, bucket, identity, limit, window_secs):
            checked.append(identity)
            return True, 0

        monkeypatch.setattr(mw.rate_limiter, "check", _check)

        served = False

        async def _app(scope, receive, send):
            nonlocal served
            served = True

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/things",
            "client": CLIENT,
            "headers": [(b"x-api-key", b"dcb_aaaaaaaa")],
        }
        await mw.RateLimitMiddleware(_app)(scope, None, lambda m: None)

        assert served is True
        assert "ip:203.0.113.9" in checked
        assert any(i.startswith("key:") for i in checked)
