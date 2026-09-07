import hashlib
import secrets
from typing import Tuple

from api.services.auth.key_environment import PRODUCTION, prefix_for


def generate_api_key(environment: str = PRODUCTION) -> Tuple[str, str, str]:
    """Generate a new API key with its hash and prefix.

    ``environment`` decides the visible prefix — ``dcb_`` for production,
    ``dcb_test_`` for sandbox. That is the point of it: a key in a config file,
    a log line or a support ticket says which world it belongs to without
    anybody having to look it up. What the key is *allowed* to do is decided by
    the stored column, never by the string.

    Returns:
        Tuple of (raw_api_key, key_hash, key_prefix)
        - raw_api_key: The actual API key to give to the user
        - key_hash: SHA256 hash of the key for storage
        - key_prefix: First characters, for display purposes
    """
    # Changing this does not invalidate anything. A key is looked up by the
    # SHA-256 of the whole string (``get_api_key_by_hash``); the prefix is
    # stored only so the UI can show which key a row refers to without holding
    # the key itself. Existing dgr_ keys keep working, and always will.
    prefix = prefix_for(environment)
    raw_api_key = f"{prefix}{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    # Long enough to include the whole environment prefix, so the list screen
    # can tell a sandbox key from a production one at a glance. Eight characters
    # cut "dcb_test_" in half and made every key look alike.
    key_prefix = raw_api_key[: len(prefix) + 4]

    return raw_api_key, key_hash, key_prefix


def hash_api_key(raw_api_key: str) -> str:
    """Hash an API key for comparison.

    Args:
        raw_api_key: The raw API key to hash

    Returns:
        SHA256 hash of the API key
    """
    return hashlib.sha256(raw_api_key.encode()).hexdigest()
