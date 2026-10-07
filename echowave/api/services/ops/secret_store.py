"""Where an activated provider key is mirrored, inside an allowlisted
namespace (handoff 34, "Provider keys screen").

The platform's provider keys live encrypted in ``platform_provider_credentials``
today, and the pipeline reads them from there. The handoff's target is AWS
Secrets Manager. This is the seam between the two: the lifecycle writes the
activated key through ``store()``, which is a no-op for the ``database``
backend and a ``PutSecretValue`` for ``aws_secrets_manager``.

**Namespace.** Every name is built here from (component, provider) under
``OPS_SECRET_NAMESPACE`` and checked against it before any call, so a caller
cannot point a write at another secret. There is no read method: nothing in
the console ever needs a secret value back.

**IAM.** The runtime role needs ``secretsmanager:PutSecretValue`` and
``secretsmanager:CreateSecret`` on ``arn:...:secret:<namespace>*`` only.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from loguru import logger

from api import constants

_SEGMENT = re.compile(r"^[a-z0-9_-]{1,64}$")


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    backend: str

    async def store(self, component: str, provider: str, value: str) -> str | None: ...

    async def delete_previous(self, component: str, provider: str) -> None: ...


def secret_name(component: str, provider: str, *, namespace: str | None = None) -> str:
    namespace = namespace if namespace is not None else constants.OPS_SECRET_NAMESPACE
    if not namespace or not namespace.endswith("/"):
        raise SecretStoreError("OPS_SECRET_NAMESPACE must be set and end with '/'.")
    for part in (component, provider):
        if not _SEGMENT.match(part or ""):
            raise SecretStoreError(f"{part!r} is not a valid secret name segment.")
    name = f"{namespace}{component}/{provider}"
    if not name.startswith(namespace):
        raise SecretStoreError("Secret name escapes the namespace.")
    return name


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return type(exc).__name__


class DatabaseSecretStore:
    """The encrypted platform table is the store; nothing to mirror."""

    backend = "database"

    async def store(self, component: str, provider: str, value: str) -> str | None:
        return None

    async def delete_previous(self, component: str, provider: str) -> None:
        return None


class AwsSecretsManagerStore:
    """Mirror into Secrets Manager. ``client`` is a boto3 secretsmanager
    client (injected in tests); calls run in a thread so the event loop is
    never blocked on AWS."""

    backend = "aws_secrets_manager"

    def __init__(self, client: Any = None, *, namespace: str | None = None):
        self._client = client
        self._namespace = namespace

    def _get_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("secretsmanager")
        return self._client

    async def store(self, component: str, provider: str, value: str) -> str | None:
        import asyncio

        name = secret_name(component, provider, namespace=self._namespace)
        client = self._get_client()

        def _put() -> str:
            try:
                client.put_secret_value(SecretId=name, SecretString=value)
            except Exception as exc:  # noqa: BLE001
                if _error_code(exc) != "ResourceNotFoundException":
                    raise
                client.create_secret(Name=name, SecretString=value)
            return name

        try:
            return await asyncio.to_thread(_put)
        except Exception as exc:  # noqa: BLE001
            # The class only: an AWS error message can echo request fields.
            logger.error(
                "Secrets Manager write for {} failed: {}", name, type(exc).__name__
            )
            raise SecretStoreError(
                f"Could not write {name}: {type(exc).__name__}"
            ) from None

    async def delete_previous(self, component: str, provider: str) -> None:
        # Secrets Manager keeps AWSPREVIOUS on its own; revoking at the
        # provider is what retires the old value. Nothing to delete here.
        return None


def get_store() -> SecretStore:
    if constants.OPS_SECRET_BACKEND == "aws_secrets_manager":
        return AwsSecretsManagerStore()
    if constants.OPS_SECRET_BACKEND != "database":
        logger.warning(
            "Unknown OPS_SECRET_BACKEND {!r}; using the database store",
            constants.OPS_SECRET_BACKEND,
        )
    return DatabaseSecretStore()
