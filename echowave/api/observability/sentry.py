"""Error reporting, the same way in every process that runs our code.

The web app started Sentry and the background worker did not, so a routine,
a campaign batch or the post-call pipeline could fail for days with nobody
told: those run in ``arq``, not uvicorn. One function, called from both.

Off unless ``SENTRY_DSN`` is set (and, on an OSS install, telemetry is on),
so a local run or a self-hosted install sends nothing anywhere.
"""

from __future__ import annotations

import sentry_sdk
from sentry_sdk.integrations.arq import ArqIntegration
from sentry_sdk.integrations.loguru import LoggingLevels, LoguruIntegration

from api.constants import DEPLOYMENT_MODE, ENABLE_TELEMETRY, SENTRY_DSN
from api.logging_config import ENVIRONMENT
from api.utils.sentry_scrub import scrub_event


def enabled() -> bool:
    return bool(SENTRY_DSN) and (DEPLOYMENT_MODE != "oss" or ENABLE_TELEMETRY)


def init(component: str) -> bool:
    """Start Sentry for this process. ``component`` is ``api`` or ``worker``,
    so an error says which one it came from. Returns whether it started."""
    if not enabled():
        return False
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=False,
        before_send=scrub_event,
        environment=ENVIRONMENT,
        integrations=[
            ArqIntegration(),
            # Most failures here are caught and logged with
            # ``logger.exception`` rather than raised -- a call must keep
            # going -- so an ERROR log is what has to reach Sentry.
            LoguruIntegration(
                level=LoggingLevels.INFO.value,
                event_level=LoggingLevels.ERROR.value,
            ),
        ],
    )
    sentry_sdk.set_tag("component", component)
    print(f"Sentry initialized for {component} in environment: {ENVIRONMENT}")
    return True
