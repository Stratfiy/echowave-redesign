"""Set up logging before importing anything else"""

import sentry_sdk

from api.constants import (
    CORS_ALLOWED_ORIGINS,
    DEPLOYMENT_MODE,
    ENABLE_TELEMETRY,
    PUBLIC_BASE_URL,
    SENTRY_DSN,
)
from api.logging_config import ENVIRONMENT, setup_logging

# Set up logging and get the listener for cleanup
setup_logging()


if SENTRY_DSN and (
    DEPLOYMENT_MODE != "oss" or (DEPLOYMENT_MODE == "oss" and ENABLE_TELEMETRY)
):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=True,
        environment=ENVIRONMENT,
    )
    print(f"Sentry initialized in environment: {ENVIRONMENT}")


from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.constants import REDIS_URL
from api.mcp_server import mcp
from api.routes.main import router as main_router
from api.services.configuration.platform_credential_seed import (
    seed_from_environment as seed_platform_credentials_from_environment,
)
from api.services.pipecat.tracing_config import (
    handle_langfuse_sync,
    load_all_org_langfuse_credentials,
)
from api.services.worker_sync.manager import (
    WorkerSyncManager,
    set_worker_sync_manager,
)
from api.services.worker_sync.protocol import WorkerSyncEventType
from api.services.workflow.launch_template_seed import seed_launch_templates
from api.tasks.arq import get_arq_redis

API_PREFIX = "/api/v1"

mcp_app = mcp.http_app(path="/", stateless_http=True)


def _warn_if_mps_is_inherited() -> None:
    """Say out loud that this deployment is depending on a host nobody chose.

    ``MPS_API_URL`` was undocumented, so an install that never set it inherited
    ``https://services.decibyl.ai`` and depended on it silently. Nothing fails
    outright any more — ingestion and transcription both run locally — so what
    remains is one question: does this deployment sell the Decibyl-managed
    model tier? If it does, that host has to be real.

    A log line rather than a refusal. The default is correct for the managed
    product, and refusing to boot over it would take down the deployment it is
    right for.
    """
    from api.constants import MPS_API_URL, MPS_API_URL_IS_DEFAULT

    if MPS_API_URL_IS_DEFAULT:
        logger.warning(
            "MPS_API_URL is unset, so this deployment has inherited the default "
            f"{MPS_API_URL}. Knowledge base ingestion and recording "
            "transcription both run locally and do not need it. It is required "
            "only for the Decibyl-managed model tier, whose service keys are "
            "issued against it. Set MPS_API_URL explicitly, or leave the "
            "managed tier unsold. See DEPLOY-ENV.md §7."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_app.lifespan(app):
        # warmup arq pool
        await get_arq_redis()

        _warn_if_mps_is_inherited()

        # Install any platform provider keys the environment declares, so a
        # freshly-deployed box serves managed accounts without someone having
        # to log in and paste keys into the staff screen first.
        await seed_platform_credentials_from_environment()

        # Install the four launch templates, so a new account picks a job to be
        # done rather than being shown an empty canvas.
        await seed_launch_templates()

        # Pre-register all org-specific Langfuse exporters so they're ready
        # before any pipeline runs, without per-call DB lookups.
        await load_all_org_langfuse_credentials()

        # Start cross-worker sync manager so config changes propagate to all workers
        sync_manager = WorkerSyncManager(REDIS_URL)
        sync_manager.register(
            WorkerSyncEventType.LANGFUSE_CREDENTIALS, handle_langfuse_sync
        )
        await sync_manager.start()
        set_worker_sync_manager(sync_manager)

        yield  # Run app

        # Shutdown sequence - this runs when FastAPI is shutting down
        logger.info("Starting graceful shutdown...")
        await sync_manager.stop()


app = FastAPI(
    title="Decibyl API",
    description="API for the Decibyl app",
    version="1.0.0",
    openapi_url=f"{API_PREFIX}/openapi.json",
    lifespan=lifespan,
    servers=[
        # Ends up as the generated client's default base URL, so it must be a
        # host that exists. Derived from PUBLIC_BASE_URL where one is set, since
        # that is already the deployment's own address.
        {
            "url": PUBLIC_BASE_URL or "https://app.decibyl.ai",
            "description": "Production",
        },
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
)


# Configure CORS.
# OSS is typically deployed with UI and API behind a single reverse proxy
# (same-origin, so CORS does not apply). Keep it permissive without
# credentials — wildcard + credentials is rejected by browsers and unsafe.
# SaaS deployments must set CORS_ALLOWED_ORIGINS to an explicit allowlist.
if DEPLOYMENT_MODE == "oss":
    cors_origins: list[str] = ["*"]
    cors_allow_credentials = False
else:
    if not CORS_ALLOWED_ORIGINS:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS must be set to an explicit origin allowlist "
            "when DEPLOYMENT_MODE != 'oss'"
        )
    if "*" in CORS_ALLOWED_ORIGINS:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS cannot contain '*' with credentialed requests"
        )
    cors_origins = CORS_ALLOWED_ORIGINS
    cors_allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _add_public_embed_cors_middleware() -> None:
    from api.routes.public_embed import PublicEmbedCORSMiddleware

    app.add_middleware(PublicEmbedCORSMiddleware, api_prefix=API_PREFIX)


_add_public_embed_cors_middleware()

api_router = APIRouter()

# include subrouters here
api_router.include_router(main_router)

# main router with api prefix
app.include_router(api_router, prefix=API_PREFIX)

# Mount the MCP server — agents reach it at /api/v1/mcp over Streamable HTTP,
# authenticating with the same X-API-Key header used by the REST API.
# Mounted under /api/v1 so existing reverse-proxy rules (nginx etc.) route it
# without any extra configuration.
app.mount(f"{API_PREFIX}/mcp", mcp_app)
