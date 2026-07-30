"""ARQ worker configuration - setup logging before importing tasks"""

import ssl
from urllib.parse import urlparse

from api.constants import REDIS_URL

# Setup logging - this is now idempotent and safe to call multiple times
from api.logging_config import setup_logging
from api.tasks.function_names import FunctionNames

setup_logging()

# Now import ARQ and task dependencies
from arq import create_pool, cron
from arq.connections import ArqRedis, RedisSettings

parsed_url = urlparse(REDIS_URL)

# Check if we're using TLS (rediss://)
use_ssl = parsed_url.scheme == "rediss"

# Create SSL context if using rediss://
ssl_context = None
if use_ssl:
    ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

REDIS_SETTINGS = RedisSettings(
    host=parsed_url.hostname or "localhost",
    port=parsed_url.port or 6379,
    password=parsed_url.password,
    conn_timeout=10,
    ssl=use_ssl,
    ssl_ca_certs=None if not use_ssl else None,
    ssl_certfile=None,
    ssl_keyfile=None,
    ssl_check_hostname=False if use_ssl else None,
)

from api.tasks.campaign_tasks import (
    process_campaign_batch,
    sync_campaign_source,
)
from api.tasks.knowledge_base_processing import process_knowledge_base_document
from api.tasks.run_integrations import run_integrations_post_workflow_run
from api.tasks.webhook_delivery import deliver_webhook, sweep_webhook_deliveries
from api.tasks.billing_rollup import refresh_billing_rollups
from api.tasks.credit_reservations import sweep_credit_reservations
from api.tasks.kyc_carrier_poll import poll_kyc_carrier_status
from api.tasks.workflow_completion import process_workflow_completion


class WorkerSettings:
    functions = [
        run_integrations_post_workflow_run,
        process_workflow_completion,
        sync_campaign_source,
        process_campaign_batch,
        process_knowledge_base_document,
        deliver_webhook,
        refresh_billing_rollups,
        poll_kyc_carrier_status,
        sweep_credit_reservations,
    ]
    cron_jobs = [
        # Safety net for webhook deliveries whose ARQ job was lost (worker
        # restart / Redis flush): re-enqueue any pending delivery that is overdue.
        cron(
            sweep_webhook_deliveries,
            minute=set(range(0, 60, 5)),
            second=0,
            run_at_startup=True,
        ),
        # The billing dashboard reads daily_organization_rollup, not
        # workflow_runs. Without this the table is never written outside the
        # seed script and every headline figure reads zero in production.
        # Every 10 minutes so the numbers are close to live, and at startup so
        # a fresh deployment is not blank until the next tick.
        cron(
            refresh_billing_rollups,
            minute=set(range(0, 60, 10)),
            second=30,
            run_at_startup=True,
        ),
        # Carriers do not call back when a compliance application is decided,
        # so an approved account would sit blocked until someone happened to
        # open the admin queue. Fifteen minutes is well inside the hours-to-days
        # these decisions actually take, and the poll is read-only on our side
        # unless the verdict changed.
        cron(
            poll_kyc_carrier_status,
            minute={0, 15, 30, 45},
            second=15,
            run_at_startup=False,
        ),
        # A call that dies with a worker leaves its funds held forever, and the
        # customer sees a balance lower than what they bought with nothing able
        # to explain it. Every five minutes so the leak stays small; at startup
        # because a worker restart is itself the most likely way holds were
        # stranded in the first place.
        cron(
            sweep_credit_reservations,
            minute=set(range(0, 60, 5)),
            second=45,
            run_at_startup=True,
        ),
    ]
    redis_settings = REDIS_SETTINGS
    max_jobs = 10


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    # --- Handlers ---
    "handlers": {
        "console": {  # everything goes to stdout
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "level": "WARNING",  # only WARNING and above
            "formatter": "simple",
        },
    },
    # --- Formatters (optional) ---
    "formatters": {
        "simple": {
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        },
    },
    # --- Root logger ---
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    # --- Optionally silence Arq itself explicitly ---
    "loggers": {
        "arq": {  # arq.* loggers
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}


_redis_pool: ArqRedis | None = None


async def get_arq_redis() -> ArqRedis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(REDIS_SETTINGS)
    return _redis_pool


async def enqueue_job(function_name: FunctionNames, *args, **kwargs):
    redis = await get_arq_redis()
    # kwargs forwards ARQ job options (e.g. _job_id, _defer_by) used for
    # deterministic, backed-off webhook delivery retries.
    return await redis.enqueue_job(function_name, *args, **kwargs)
