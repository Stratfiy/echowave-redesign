"""Set up logging before importing anything else"""

from api.constants import CORS_ALLOWED_ORIGINS, DEPLOYMENT_MODE, PUBLIC_BASE_URL
from api.logging_config import setup_logging
from api.observability import sentry

# Set up logging and get the listener for cleanup
setup_logging()
sentry.init("api")


from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.constants import REDIS_URL
from api.mcp_server import mcp
from api.routes.main import router as main_router
from api.services import features
from api.services.configuration.managed_tiers import (
    refresh_overrides as refresh_managed_tier_overrides,
)
from api.services.configuration.platform_credential_seed import (
    seed_from_environment as seed_platform_credentials_from_environment,
)
from api.services.ops_alerts import watchdog as ops_alerts_watchdog
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


async def _handle_managed_tier_sync(event) -> None:
    """Another worker changed a tier mapping; re-read it here.

    The event is only a trigger — the authoritative state is the table, which
    is why this reloads rather than applying anything carried in the message.
    """
    await refresh_managed_tier_overrides()


async def _handle_feature_override_sync(event) -> None:
    """A feature was switched from the staff console; re-read the table."""
    await features.refresh_overrides()


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

        # Operator-chosen tier mappings, before any call can resolve one. A
        # worker that served a call before this ran would use the compiled
        # default and bill against a vendor nobody selected.
        await refresh_managed_tier_overrides()

        # Feature switches set from the staff console (ADMIN-1), before any
        # request can ask whether a feature is on. Re-read every 30 s as a
        # backstop for a missed sync event.
        await features.refresh_overrides()
        features.start_periodic_refresh()

        # The worker cannot report its own death, so the API watches it
        # (services/ops_alerts/watchdog.py). Idle while ops_alerts is off.
        ops_alerts_watchdog.start()

        # Start cross-worker sync manager so config changes propagate to all workers
        sync_manager = WorkerSyncManager(REDIS_URL)
        sync_manager.register(
            WorkerSyncEventType.LANGFUSE_CREDENTIALS, handle_langfuse_sync
        )
        # Which vendor serves each managed tier. Cached per worker so that
        # `managed_tiers.resolve` can stay synchronous, which is why a change
        # made on one worker has to be announced to the rest.
        sync_manager.register(
            WorkerSyncEventType.MANAGED_TIERS, _handle_managed_tier_sync
        )
        sync_manager.register(
            WorkerSyncEventType.FEATURE_OVERRIDES, _handle_feature_override_sync
        )
        await sync_manager.start()
        set_worker_sync_manager(sync_manager)

        yield  # Run app

        # Shutdown sequence - this runs when FastAPI is shutting down
        logger.info("Starting graceful shutdown...")
        await sync_manager.stop()
        await features.stop_periodic_refresh()
        await ops_alerts_watchdog.stop()


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


# The document served at openapi_url is the public surface: staff-only
# operations are enforced by their routers and are not described to the
# world. See services/openapi_surface.py.
from api.services import openapi_surface  # noqa: E402
from api.services.refused import Refused  # noqa: E402


@app.exception_handler(Refused)
async def _refused(_request, exc: Refused):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": str(exc)})


openapi_surface.install(app)


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


def _add_rate_limit_middleware() -> None:
    # Added before CORS, so CORS wraps it: a refused request is still
    # counted and refused before routing, auth or a database session, and
    # its 429 carries the CORS headers. Outside CORS, a browser on another
    # origin could not read the 429 or its Retry-After and saw only "Failed
    # to fetch" (tests/test_rate_limit_answers_cross_origin.py).
    from api.middleware_rate_limit import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware)


_add_rate_limit_middleware()

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


# Outermost, so everything behind it -- the rate limit included -- sees the
# route's own path. See services/api_paths.py.
from api.services.api_paths import PluralPaths  # noqa: E402

app.add_middleware(PluralPaths, routes_of=lambda: app.routes)

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
