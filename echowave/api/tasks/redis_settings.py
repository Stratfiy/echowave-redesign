"""Redis connection settings for the ARQ worker.

Kept apart from ``tasks/arq.py`` so the TLS policy can be tested without
importing every task module.

Before KAN-245 a ``rediss://`` URL was accepted with certificate verification
off and no hostname check, which made the TLS on the queue decorative: any
box on the path could present any certificate. Verification is now on by
default. ``REDIS_SSL_CA_CERTS`` points at a CA bundle for a private CA;
``REDIS_SSL_VERIFY=false`` is the emergency escape hatch and is logged loudly
so it never becomes the quiet default again.
"""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings
from loguru import logger


def build_redis_settings(
    redis_url: str,
    *,
    ca_certs: str | None = None,
    verify: bool = True,
    conn_timeout: int = 10,
) -> RedisSettings:
    parsed = urlparse(redis_url)
    use_ssl = parsed.scheme == "rediss"

    if use_ssl and not verify:
        logger.warning(
            "REDIS_SSL_VERIFY is off: the worker will accept any certificate "
            "from {host}. Set it back to true as soon as the CA bundle is in "
            "place (REDIS_SSL_CA_CERTS).",
            host=parsed.hostname,
        )

    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        conn_timeout=conn_timeout,
        ssl=use_ssl,
        ssl_ca_certs=ca_certs if use_ssl else None,
        ssl_certfile=None,
        ssl_keyfile=None,
        ssl_cert_reqs=("required" if verify else "none") if use_ssl else None,
        ssl_check_hostname=verify if use_ssl else None,
    )
