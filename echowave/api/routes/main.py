import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from loguru import logger
from pydantic import BaseModel

from api.db.models import UserModel
from api.routes.admin_console import router as admin_console_router
from api.routes.admin_kpis import router as admin_kpis_router
from api.routes.agent_builder import router as agent_builder_router
from api.routes.agent_graph import router as agent_graph_router
from api.routes.agent_options import router as agent_options_router
from api.routes.agent_stream import router as agent_stream_router
from api.routes.agent_templates import router as agent_templates_router
from api.routes.agent_timeline import router as agent_timeline_router
from api.routes.auth import router as auth_router
from api.routes.billing_dashboard import router as billing_dashboard_router
from api.routes.bot_event_webhooks import router as bot_event_webhooks_router
from api.routes.bot_triggers import router as bot_triggers_router
from api.routes.browser import admin_router as browser_admin_router
from api.routes.browser import router as browser_router
from api.routes.budgets import router as budgets_router
from api.routes.campaign import router as campaign_router
from api.routes.care import router as care_router
from api.routes.channel_links import router as channel_links_router
from api.routes.connectors import router as connectors_router
from api.routes.contacts import router as contacts_router
from api.routes.controls import router as controls_router
from api.routes.controls_admin import router as controls_admin_router
from api.routes.cost_estimate import router as cost_estimate_router
from api.routes.credentials import router as credentials_router
from api.routes.desktop import router as desktop_router
from api.routes.dialer_connections import router as dialer_connections_router
from api.routes.do_not_call import router as do_not_call_router
from api.routes.evals import router as evals_router
from api.routes.extraction_library import router as extraction_library_router
from api.routes.feature_admin import router as feature_admin_router
from api.routes.folder import router as folder_router
from api.routes.helpers import router as helpers_router
from api.routes.identity import router as identity_router
from api.routes.impersonation import router as impersonation_router
from api.routes.knowledge_base import router as knowledge_base_router
from api.routes.kyc import router as kyc_router
from api.routes.kyc_admin import router as kyc_admin_router
from api.routes.learning import router as learning_router
from api.routes.managed_numbers import router as managed_numbers_router
from api.routes.meetings import router as meetings_router
from api.routes.missed_calls import router as missed_calls_router
from api.routes.node_types import router as node_types_router
from api.routes.notifications import router as notifications_router
from api.routes.onboarding import router as onboarding_router
from api.routes.ops_console import router as ops_console_router
from api.routes.organisation import router as organisation_router
from api.routes.organisation_memory import router as organisation_memory_router
from api.routes.organization import router as organization_router
from api.routes.organization_members import router as organization_members_router
from api.routes.organization_usage import router as organization_usage_router
from api.routes.packs import router as packs_router
from api.routes.partner_admin import router as partner_admin_router
from api.routes.partners import router as partners_router
from api.routes.payments import router as payments_router
from api.routes.platform_credentials import router as platform_credentials_router
from api.routes.privacy import router as privacy_router
from api.routes.procurement import router as procurement_router
from api.routes.promo_admin import router as promo_admin_router
from api.routes.provider_keys import router as provider_keys_router
from api.routes.public_agent import router as public_agent_router
from api.routes.public_decibyl_channels import router as public_decibyl_channels_router
from api.routes.public_download import router as public_download_router
from api.routes.public_email import router as public_email_router
from api.routes.public_embed import router as public_embed_router
from api.routes.public_marketplace import router as public_marketplace_router
from api.routes.public_triggers import router as public_triggers_router
from api.routes.public_trust import router as public_trust_router
from api.routes.public_whatsapp import router as public_whatsapp_router
from api.routes.reach import router as reach_router
from api.routes.referrals import router as referrals_router
from api.routes.reports import router as reports_router
from api.routes.routines import all_router as all_routines_router
from api.routes.routines import router as routines_router
from api.routes.s3_signed_url import router as s3_router
from api.routes.service_keys import router as service_keys_router
from api.routes.settings import router as settings_router
from api.routes.shell import public_router as public_early_access_router
from api.routes.shell import router as shell_router
from api.routes.skills import router as skills_router
from api.routes.staff_console import router as staff_console_router
from api.routes.studio import public_router as public_studio_router
from api.routes.studio import router as studio_router
from api.routes.superuser import router as superuser_router
from api.routes.support import router as support_router
from api.routes.support_admin import actions_router as support_actions_router
from api.routes.support_admin import router as support_admin_router
from api.routes.tasks import router as tasks_router
from api.routes.team import router as team_router
from api.routes.telephony import router as telephony_router
from api.routes.telephony_admin import router as telephony_admin_router
from api.routes.today import router as today_router
from api.routes.tool import router as tool_router
from api.routes.tool_library import router as tool_library_router
from api.routes.translate import router as translate_router
from api.routes.turn_credentials import router as turn_credentials_router
from api.routes.user import router as user_router
from api.routes.verified_numbers import router as verified_numbers_router
from api.routes.voice import admin_router as voice_admin_router
from api.routes.voice import router as voice_router
from api.routes.webrtc_signaling import router as webrtc_signaling_router
from api.routes.workflow import router as workflow_router
from api.routes.workflow_embed import router as workflow_embed_router
from api.routes.workflow_outcomes import router as workflow_outcomes_router
from api.routes.workflow_recording import router as workflow_recording_router
from api.routes.workflow_spend import router as workflow_spend_router
from api.routes.workflow_text_chat import router as workflow_text_chat_router
from api.routes.workspace_roles import router as workspace_roles_router
from api.services import features
from api.services.auth.depends import get_user
from api.services.integrations import all_routers

# No tag here on purpose. A parent tag is merged onto every child route, so
# for years every operation carried "main" beside its own tag and a router
# that forgot to tag itself was indistinguishable from one that had. Now a
# router declares its own tag or the guard in
# tests/test_public_api_is_grouped.py names it.
router = APIRouter(
    responses={404: {"description": "Not found"}},
)

router.include_router(telephony_router)
router.include_router(telephony_admin_router)
router.include_router(superuser_router)
router.include_router(feature_admin_router)
router.include_router(ops_console_router)
router.include_router(billing_dashboard_router)
router.include_router(admin_kpis_router)
router.include_router(admin_console_router)
router.include_router(impersonation_router)
router.include_router(onboarding_router)
router.include_router(referrals_router)
router.include_router(promo_admin_router)
router.include_router(agent_builder_router)
router.include_router(studio_router)
router.include_router(desktop_router)
router.include_router(browser_router)
router.include_router(browser_admin_router)
router.include_router(agent_templates_router)
router.include_router(agent_options_router)
router.include_router(cost_estimate_router)
router.include_router(kyc_router)
router.include_router(kyc_admin_router)
router.include_router(partners_router)
router.include_router(partner_admin_router)
router.include_router(managed_numbers_router)
router.include_router(organization_members_router)
router.include_router(platform_credentials_router)
router.include_router(provider_keys_router)
router.include_router(dialer_connections_router)
router.include_router(workspace_roles_router)
router.include_router(procurement_router)
router.include_router(agent_graph_router)
router.include_router(payments_router)
router.include_router(privacy_router)
router.include_router(notifications_router)
router.include_router(evals_router)
router.include_router(do_not_call_router)
router.include_router(missed_calls_router)
router.include_router(verified_numbers_router)
router.include_router(workflow_router)
router.include_router(workflow_text_chat_router)
router.include_router(user_router)
router.include_router(campaign_router)
router.include_router(credentials_router)
router.include_router(connectors_router)
router.include_router(routines_router)
router.include_router(all_routines_router)
router.include_router(bot_event_webhooks_router)
router.include_router(bot_triggers_router)
router.include_router(skills_router)
router.include_router(tasks_router)
router.include_router(controls_router)
router.include_router(helpers_router)
router.include_router(care_router)
router.include_router(controls_admin_router)
router.include_router(today_router)
router.include_router(staff_console_router)
router.include_router(settings_router)
router.include_router(voice_router)
router.include_router(voice_admin_router)
router.include_router(organisation_router)
router.include_router(organisation_memory_router)
router.include_router(packs_router)
router.include_router(team_router)
router.include_router(workflow_outcomes_router)
router.include_router(workflow_spend_router)
router.include_router(agent_timeline_router)
router.include_router(translate_router)
router.include_router(tool_router)
router.include_router(organization_router)
router.include_router(s3_router)
router.include_router(service_keys_router)
router.include_router(organization_usage_router)
router.include_router(budgets_router)
router.include_router(reports_router)
router.include_router(webrtc_signaling_router)
router.include_router(turn_credentials_router)
router.include_router(public_embed_router)
router.include_router(public_agent_router)
router.include_router(public_triggers_router)
router.include_router(public_email_router)
router.include_router(public_whatsapp_router)
router.include_router(public_decibyl_channels_router)
router.include_router(channel_links_router)
router.include_router(public_download_router)
router.include_router(public_studio_router)
router.include_router(public_trust_router)
router.include_router(public_early_access_router)
router.include_router(shell_router)
router.include_router(reach_router)
router.include_router(learning_router)
router.include_router(meetings_router)
router.include_router(support_router)
router.include_router(support_admin_router)
router.include_router(support_actions_router)
router.include_router(identity_router)
router.include_router(public_marketplace_router)
router.include_router(workflow_embed_router)
router.include_router(knowledge_base_router)
router.include_router(workflow_recording_router)
router.include_router(folder_router)
router.include_router(auth_router)
router.include_router(node_types_router)
router.include_router(extraction_library_router)
router.include_router(tool_library_router)
router.include_router(contacts_router)
router.include_router(agent_stream_router)

for _integration_router in all_routers():
    router.include_router(_integration_router)


class HealthResponse(BaseModel):
    status: str
    version: str
    backend_api_endpoint: str
    # Public URL the deployment is reachable at when it sits behind a Cloudflare
    # tunnel (the host has no public IP). null for a directly-reachable deployment.
    # The UI shows this so operators know the URL telephony providers should call.
    tunnel_url: str | None = None
    deployment_mode: str
    auth_provider: str
    turn_enabled: bool
    force_turn_relay: bool
    signup_enabled: bool
    # Public Stack Auth client config — only populated when auth_provider == "stack".
    # The UI reads these at runtime to initialize Stack, so they no longer need to
    # be baked into the browser bundle at build time. Both are public values.
    stack_project_id: str | None = None
    stack_publishable_client_key: str | None = None
    # Which switched-off features are on (services/features.py). The UI reads
    # them here, once at start-up, instead of calling each feature's route.
    features: dict[str, bool] = {}


@router.get("/features", tags=["health"])
async def organization_features(
    user: UserModel = Depends(get_user),
) -> dict[str, bool]:
    """The switched-off features as the signed-in organisation sees them:
    the global switches plus its overrides -- rows set from the staff console
    (ADMIN-1) first, then ``FEATURE_ORG_OVERRIDES`` (FLAG-1). The UI merges
    this over the global map from ``/health``."""
    return features.for_organization(user.selected_organization_id)


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    from api.constants import (
        APP_VERSION,
        AUTH_PROVIDER,
        BACKEND_API_ENDPOINT,
        DEPLOYMENT_MODE,
        ENABLE_SIGNUP,
        FORCE_TURN_RELAY,
        STACK_AUTH_PROJECT_ID,
        STACK_PUBLISHABLE_CLIENT_KEY,
        TURN_SECRET,
    )
    from api.utils.common import get_backend_endpoints, is_local_or_private_url

    logger.debug("Health endpoint called")
    backend_endpoint, _ = await get_backend_endpoints()
    # tunnel_url is set only when a Cloudflare tunnel was actually resolved: the
    # configured address isn't publicly reachable, but get_backend_endpoints found
    # a public tunnel URL for it. This is the URL the UI shows for inbound webhooks.
    # It stays null for a directly-reachable (public IP / domain) deployment, where
    # backend_api_endpoint itself is the public URL.
    tunnel_url = (
        backend_endpoint
        if is_local_or_private_url(BACKEND_API_ENDPOINT)
        and not is_local_or_private_url(backend_endpoint)
        else None
    )
    is_stack = AUTH_PROVIDER == "stack"
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        backend_api_endpoint=BACKEND_API_ENDPOINT,
        tunnel_url=tunnel_url,
        deployment_mode=DEPLOYMENT_MODE,
        auth_provider=AUTH_PROVIDER,
        turn_enabled=bool(TURN_SECRET),
        force_turn_relay=FORCE_TURN_RELAY,
        features=features.public(),
        signup_enabled=ENABLE_SIGNUP,
        stack_project_id=STACK_AUTH_PROJECT_ID if is_stack else None,
        stack_publishable_client_key=(
            STACK_PUBLISHABLE_CLIENT_KEY if is_stack else None
        ),
    )


class ActiveCallsResponse(BaseModel):
    active_calls: int


DECIBYL_DEVOPS_SECRET_HEADER = "X-Decibyl-Devops-Secret"


def _verify_devops_secret(
    configured_secret: str | None,
    provided_secret: str | None,
) -> None:
    if not configured_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Devops secret is not configured",
        )
    if not provided_secret or not secrets.compare_digest(
        provided_secret,
        configured_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )


@router.get("/health/active-calls", response_model=ActiveCallsResponse, tags=["health"])
async def active_calls(
    x_decibyl_devops_secret: Annotated[
        str | None,
        Header(alias=DECIBYL_DEVOPS_SECRET_HEADER),
    ] = None,
) -> ActiveCallsResponse:
    """In-flight call count for THIS worker — the drain signal for deploys.

    A deploy orchestrator polls this per worker and waits for zero before
    sending SIGTERM, because uvicorn force-closes live call WebSockets (close
    code 1012) on SIGTERM and would cut calls mid-conversation otherwise. The
    count is per-process: one uvicorn per VM port (scripts/rolling_update.sh)
    or per Kubernetes pod (preStop hook). See api/services/pipecat/active_calls.py.
    """
    from api.constants import DECIBYL_DEVOPS_SECRET
    from api.services.pipecat.active_calls import active_call_count

    _verify_devops_secret(DECIBYL_DEVOPS_SECRET, x_decibyl_devops_secret)
    return ActiveCallsResponse(active_calls=active_call_count())


class WorkerHealthResponse(BaseModel):
    # Tri-state: true alive, false dead, null means no heartbeat on record or
    # Redis unreachable. "Never started" and "stopped an hour ago" need
    # different responses, so they are not collapsed into one boolean.
    alive: bool | None = None
    last_seen: str | None = None
    age_seconds: float | None = None
    stale_after_seconds: int
    interval_seconds: int | None = None
    detail: str


@router.get("/health/workers", response_model=WorkerHealthResponse, tags=["health"])
async def worker_health_check(
    x_decibyl_devops_secret: Annotated[
        str | None,
        Header(alias=DECIBYL_DEVOPS_SECRET_HEADER),
    ] = None,
) -> WorkerHealthResponse:
    """Is the background worker alive — the thing `/health` cannot tell you.

    One container runs uvicorn and the ARQ worker together, so `/health`
    answering ok says nothing about whether calls are being costed, rollups
    refreshed or invoices issued. When the worker dies all of that stops
    silently and every other endpoint keeps working.

    Alert on `alive` being anything other than true. Behind the devops secret
    like `/health/active-calls`, since both are operational signals for
    monitoring rather than for callers.
    """
    from api.constants import DECIBYL_DEVOPS_SECRET
    from api.services.worker_health import worker_health

    _verify_devops_secret(DECIBYL_DEVOPS_SECRET, x_decibyl_devops_secret)
    return WorkerHealthResponse(**await worker_health())
