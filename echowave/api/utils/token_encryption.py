"""Encryption at rest for long-lived, high-value credentials (OAuth tokens).

Distinct from services/configuration/masking.py, which only masks secrets
for API *display* — this module encrypts/decrypts the actual bytes stored
in the database. OAuth refresh tokens are broad, long-lived account access;
they are encrypted here even though other stored credentials in this
codebase (webhook credentials, service keys) are not, because their blast
radius on leak is materially larger.
"""

from cryptography.fernet import Fernet, InvalidToken

from api.constants import TOKEN_ENCRYPTION_KEY


class TokenEncryptionNotConfigured(RuntimeError):
    """TOKEN_ENCRYPTION_KEY is unset; token storage/decryption is unavailable."""


def _fernet() -> Fernet:
    if not TOKEN_ENCRYPTION_KEY:
        raise TokenEncryptionNotConfigured(
            "TOKEN_ENCRYPTION_KEY is not configured. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
    key = TOKEN_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for storage. Returns a urlsafe-base64 string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a token previously produced by encrypt_token()."""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt stored token — TOKEN_ENCRYPTION_KEY may have "
            "changed since this token was stored."
        ) from e
