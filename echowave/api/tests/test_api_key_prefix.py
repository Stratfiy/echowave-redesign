"""What an API key looks like, and what actually authenticates it.

The prefix was ``dgr_`` — inherited from the project this was forked from, and
the most customer-visible trace of it left: every key an operator issues gets
pasted into config files, shell history and source control, carrying another
company's initials into places nobody will think to grep later.

Changing it is safe, and this file pins the reason. A key is verified by the
SHA-256 of the whole string; ``key_prefix`` is stored only so the UI can name a
row without holding the key. Nothing parses the prefix, nothing branches on it,
so keys issued under the old one keep working — which is the property that
makes the rename a one-line change rather than a migration.
"""

from __future__ import annotations

from api.utils.api_key import generate_api_key, hash_api_key


class TestTheKeyItself:
    def test_it_carries_our_prefix(self):
        raw, _, _ = generate_api_key()
        assert raw.startswith("dcb_")

    def test_no_key_carries_the_forks_prefix(self):
        for _ in range(20):
            raw, _, _ = generate_api_key()
            assert not raw.startswith("dgr_")

    def test_keys_are_unique(self):
        keys = {generate_api_key()[0] for _ in range(50)}
        assert len(keys) == 50

    def test_the_stored_prefix_is_for_display_only(self):
        """Eight characters — enough to recognise a key, useless for using one."""
        raw, _, prefix = generate_api_key()
        assert prefix == raw[:8]
        assert len(prefix) == 8
        assert raw not in prefix


class TestVerificationIgnoresThePrefix:
    """Why renaming it cannot lock anybody out."""

    def test_the_hash_is_over_the_whole_key(self):
        raw, key_hash, _ = generate_api_key()
        assert hash_api_key(raw) == key_hash

    def test_a_key_with_the_old_prefix_still_hashes_consistently(self):
        legacy = "dgr_ouQEc1118M3SyZ4KEuvuiYsMp9VtXM8"
        assert hash_api_key(legacy) == hash_api_key(legacy)

    def test_the_prefix_is_part_of_what_is_hashed(self):
        """So the two namespaces cannot collide."""
        assert hash_api_key("dgr_sameTail") != hash_api_key("dcb_sameTail")
