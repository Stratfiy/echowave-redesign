"""Encryption at rest for carrier credentials.

Every other secret in this codebase is Fernet-encrypted before it is stored --
the customer's provider keys (``configuration/organization_credentials``) and
Decibyl's own (``configuration/platform_credentials``) -- and both modules say
so in their docstrings. Carrier credentials were the exception: they went into
``telephony_configurations.credentials``, a plain JSONB column, in cleartext.

The exception was not deliberate. ``organization_credentials`` even explains
itself by pointing here -- "carrier credentials live on telephony
configurations, with the KYC that goes with them" -- and that home did not
encrypt them. A read-only replica, a backup, or one SQL-injection away, and
``SELECT credentials FROM telephony_configurations`` yields every tenant's
carrier token: enough to place calls billed to that customer, release their
numbers, or repoint their inbound answer_url.

**What is and is not encrypted, and why the line is there.**

Encrypted: every field a provider marks ``sensitive`` -- auth tokens, API keys,
passwords, private keys.

Not encrypted: the provider's *account id* field (Plivo's ``auth_id``, Twilio's
``account_sid``). Inbound dispatch resolves which organization a call belongs to
with ``credentials ->> 'auth_id' = :account_id`` **in SQL**
(``find_inbound_route_by_account``), so encrypting it would break inbound
routing for every tenant at once. It is an identifier rather than a secret --
the carrier puts it in the webhooks it sends us in cleartext -- and it is
useless without the token beside it, which is encrypted.

**Rollout is designed to be undramatic.** ``decrypt`` accepts both shapes, so a
row written before this existed keeps working and is upgraded the next time it
is saved; the migration rewrites the rest in one pass. With no
``PLATFORM_CREDENTIAL_SECRET`` configured, ``encrypt`` returns the credentials
untouched and says so once -- a deployment that has not set the secret must
degrade to today's behaviour rather than lose the ability to save a carrier
configuration.
"""

from __future__ import annotations

from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from api.constants import PLATFORM_CREDENTIAL_SECRET
from api.services.telephony import registry as telephony_registry

#: Marks a value this module wrote. A Fernet token is urlsafe-base64 and never
#: contains ':', so no plaintext credential can be mistaken for one, and
#: encrypting an already-encrypted value is a no-op rather than a double wrap.
PREFIX = "encv1:"


def _cipher() -> Fernet | None:
    if not PLATFORM_CREDENTIAL_SECRET:
        return None
    try:
        return Fernet(PLATFORM_CREDENTIAL_SECRET.encode())
    except Exception as exc:  # noqa: BLE001 -- reported, never raised at callers
        logger.error(
            "PLATFORM_CREDENTIAL_SECRET is not a valid Fernet key, so carrier "
            "credentials cannot be encrypted: {}",
            exc,
        )
        return None


def encryptable_fields(provider: str) -> set[str]:
    """Sensitive fields for this provider, minus the account id.

    Empty for an unknown provider, which means "encrypt nothing" -- the safe
    direction, because the alternative is encrypting a field something else
    matches on and breaking it.
    """
    spec = telephony_registry.get_optional(provider)
    if spec is None or not getattr(spec, "ui_metadata", None):
        return set()

    account_field = getattr(spec, "account_id_credential_field", "") or ""
    return {
        field.name
        for field in spec.ui_metadata.fields
        if getattr(field, "sensitive", False) and field.name != account_field
    }


def encrypt(provider: str, credentials: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the credentials with sensitive values encrypted.

    Never raises. A configuration that cannot be saved is an outage; a
    credential stored the way it always used to be is the status quo.
    """
    values = dict(credentials or {})
    fields = encryptable_fields(provider)
    if not fields:
        return values

    cipher = _cipher()
    if cipher is None:
        logger.warning(
            "PLATFORM_CREDENTIAL_SECRET is not configured, so {} credentials "
            "are being stored unencrypted.",
            provider,
        )
        return values

    for name in fields:
        value = values.get(name)
        if not isinstance(value, str) or not value or value.startswith(PREFIX):
            continue
        values[name] = PREFIX + cipher.encrypt(value.encode()).decode()
    return values


def decrypt(provider: str, credentials: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the credentials with any encrypted values readable again.

    Tolerant by design. A value without the marker is returned as it is, which
    is what makes a row written before this module keep working; a value that
    cannot be decrypted is left alone and logged, so a rotated secret surfaces
    as one provider failing to authenticate rather than as an unhandled
    exception on every telephony read path.
    """
    values = dict(credentials or {})
    cipher = _cipher()

    for name, value in list(values.items()):
        if not isinstance(value, str) or not value.startswith(PREFIX):
            continue
        if cipher is None:
            logger.error(
                "{} credential '{}' is encrypted but PLATFORM_CREDENTIAL_SECRET "
                "is not configured, so it cannot be read.",
                provider,
                name,
            )
            continue
        try:
            values[name] = cipher.decrypt(value[len(PREFIX) :].encode()).decode()
        except InvalidToken:
            logger.error(
                "{} credential '{}' could not be decrypted — the encryption "
                "secret may have been rotated.",
                provider,
                name,
            )
    return values
