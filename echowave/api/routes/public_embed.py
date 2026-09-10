"""Public API endpoints for workflow embedding.

These endpoints are accessible without authentication but require valid embed tokens.
They handle CORS, domain validation, and session management for embedded workflows.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.constants import BACKEND_API_ENDPOINT, UI_APP_URL
from api.db import db_client
from api.enums import WorkflowRunMode
from api.routes.turn_credentials import (
    TURN_SECRET,
    TurnCredentialsResponse,
    generate_turn_credentials,
)
from api.services.embed_logo import (
    is_own_logo_key,
    logo_from_settings,
    public_settings,
)
from api.services.storage import (
    get_current_storage_backend,
    get_storage_for_backend,
)

router = APIRouter(prefix="/public/embed")

EMBED_CORS_ALLOW_HEADERS = "Content-Type, Origin"
EMBED_CORS_MAX_AGE = "86400"


class InitEmbedRequest(BaseModel):
    """Request model for initializing an embed session"""

    token: str
    context_variables: Optional[dict] = None
    #: "voice" or "text". A visitor in an office, on a train, or who simply
    #: does not want to talk out loud is most of the traffic a website widget
    #: sees — a voice-only widget is a widget most visitors close. Same agent,
    #: same setup, different transport.
    mode: str = "voice"


class InitEmbedResponse(BaseModel):
    """Response model for embed initialization"""

    session_token: str
    workflow_run_id: int
    config: dict


# The signed URL the logo redirect points at. Short, because the redirect is
# public and a leaked one should stop working quickly.
LOGO_URL_TTL_SECONDS = 3600

# How long a browser may cache the redirect itself. Must stay comfortably below
# LOGO_URL_TTL_SECONDS: cache the 302 for longer than its target lives and some
# visitors follow a link that has already expired, which shows as a broken
# image on the customer's site and nowhere in our logs.
LOGO_CACHE_SECONDS = 600


class EmbedConfigResponse(BaseModel):
    """Response model for embed configuration"""

    workflow_id: int
    settings: dict
    theme: str
    position: str
    button_text: str
    button_color: str
    size: str
    auto_start: bool
    #: What the share page calls the agent. None for a widget on a customer's
    #: own site, which names it itself.
    agent_name: str | None = None
    # Absent when the account has not uploaded one, which is most of them.
    logo_url: str | None = None


def is_platform_origin(origin: str) -> bool:
    """Is this our own app? Host and port both, because on a developer's
    machine every port is a different site and a token that let any
    localhost port through would be the widget's own whitelist undone."""
    if not origin or not UI_APP_URL:
        return False
    ours, our_port = _parse_origin_host_port(str(UI_APP_URL))
    theirs, their_port = _parse_origin_host_port(origin)
    return bool(ours) and ours == theirs and (our_port or None) == (their_port or None)


def validate_origin(origin: str, allowed_domains: list) -> bool:
    """Is this origin allowed to use the token it presented?

    **An empty list denies everything.** It used to allow everything, which
    made "I have not filled this in yet" and "anybody may embed this" the same
    state — and the first is the state every token starts in. An embed token is
    a bearer credential that runs on a public page: the script tag is readable
    by anyone who views source, so a token that accepts any origin can be
    lifted onto someone else's site and dial on the issuing account's balance.
    Unrestricted has to be typed, not defaulted into, so ``*`` remains
    available below and empty means no.

    This is deliberately a breaking change for tokens that never set a domain.
    Failing closed shows up as a widget that stops loading, which someone
    reports in a day; failing open shows up as a bill, which nobody reports at
    all.

    Args:
        origin: The origin header from the request
        allowed_domains: List of allowed domain patterns

    Returns:
        True if origin is allowed, False otherwise
    """
    # The hosted share page is ours: a token whose owner gave out a link
    # must work there without the owner having to whitelist our own host.
    if is_platform_origin(origin):
        return True

    if not allowed_domains:
        return False

    domain, origin_port = _parse_origin_host_port(origin)
    if not domain:
        return False

    # Normalize domain for www matching
    def normalize_www(d: str) -> tuple[str, str]:
        """Return both www and non-www versions of a domain"""
        if d.startswith("www."):
            return (d, d[4:])  # (www.x.com, x.com)
        else:
            return (d, f"www.{d}")  # (x.com, www.x.com)

    domain_variants = normalize_www(domain)

    for allowed in allowed_domains:
        allowed = str(allowed).strip().lower()
        if allowed == "*":
            return True
        allowed_domain, allowed_port = _parse_origin_host_port(allowed)
        if not allowed_domain:
            continue
        if allowed_port is not None and allowed_port != origin_port:
            continue

        if allowed_domain.startswith("*."):
            # Wildcard subdomain matching
            base_domain = allowed_domain[2:]
            if domain == base_domain or domain.endswith("." + base_domain):
                return True
        else:
            # Check both www and non-www versions
            allowed_variants = normalize_www(allowed_domain)
            # If any variant of domain matches any variant of allowed, it's valid
            if any(
                dv in allowed_variants or av in domain_variants
                for dv in domain_variants
                for av in allowed_variants
            ):
                return True

    return False


def _parse_origin_host_port(value: str) -> tuple[str, str | None]:
    candidate = value.strip().lower()
    if not candidate:
        return "", None

    if "://" not in candidate and not candidate.startswith("//"):
        candidate = f"//{candidate}"

    parsed = urlsplit(candidate)
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None

    port = str(parsed_port) if parsed_port is not None else None
    return (parsed.hostname or "").rstrip("."), port


def generate_session_token() -> str:
    """Generate a cryptographically secure session token"""
    return f"emb_session_{secrets.token_urlsafe(32)}"


def get_request_origin(request: Request) -> str:
    """Extract origin from request headers, falling back to referer if not present."""
    origin = request.headers.get("origin", "")
    if not origin:
        origin = request.headers.get("referer", "")
    return origin


def _cors_response(origin: str, methods: str) -> Response:
    return Response(
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": methods,
            "Access-Control-Allow-Headers": EMBED_CORS_ALLOW_HEADERS,
            "Access-Control-Max-Age": EMBED_CORS_MAX_AGE,
            "Vary": "Origin",
        }
    )


def _allow_embed_origin(response: Response, origin: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    vary = response.headers.get("Vary")
    if not vary:
        response.headers["Vary"] = "Origin"
        return

    vary_values = {value.strip().lower() for value in vary.split(",")}
    if "origin" not in vary_values:
        response.headers["Vary"] = f"{vary}, Origin"


async def _config_preflight_response(token: str, origin: str) -> Response:
    embed_token = await db_client.get_embed_token_by_token(token)
    if not embed_token or not embed_token.is_active:
        return Response(status_code=403)

    if not validate_origin(origin, embed_token.allowed_domains or []):
        return Response(status_code=403)

    return _cors_response(origin, "GET, OPTIONS")


async def _turn_credentials_preflight_response(
    session_token: str, origin: str
) -> Response:
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        return Response(status_code=403)

    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        return Response(status_code=403)

    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        return Response(status_code=403)

    if not validate_origin(origin, embed_token.allowed_domains or []):
        return Response(status_code=403)

    return _cors_response(origin, "GET, OPTIONS")


async def _text_message_preflight_response(session_token: str, origin: str) -> Response:
    """Same session and domain checks as the voice path, POST instead of GET."""
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        return Response(status_code=403)

    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        return Response(status_code=403)

    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        return Response(status_code=403)

    if not validate_origin(origin, embed_token.allowed_domains or []):
        return Response(status_code=403)

    return _cors_response(origin, "POST, OPTIONS")


async def build_public_embed_preflight_response(
    path: str, origin: str, requested_method: str, api_prefix: str = "/api/v1"
) -> Response | None:
    """Handle embed preflights before global CORSMiddleware rejects external sites."""
    public_embed_prefix = f"{api_prefix.rstrip('/')}/public/embed"

    if path == f"{public_embed_prefix}/init":
        if requested_method.upper() != "POST":
            return Response(status_code=405)
        return _cors_response(origin, "POST, OPTIONS")

    config_prefix = f"{public_embed_prefix}/config/"
    if path.startswith(config_prefix):
        if requested_method.upper() != "GET":
            return Response(status_code=405)
        token = path[len(config_prefix) :].split("/", 1)[0]
        return await _config_preflight_response(token, origin)

    text_prefix = f"{public_embed_prefix}/text/"
    if path.startswith(text_prefix):
        if requested_method.upper() != "POST":
            return Response(status_code=405)
        session_token = path[len(text_prefix) :].split("/", 1)[0]
        return await _text_message_preflight_response(session_token, origin)

    turn_credentials_prefix = f"{public_embed_prefix}/turn-credentials/"
    if path.startswith(turn_credentials_prefix):
        if requested_method.upper() != "GET":
            return Response(status_code=405)
        session_token = path[len(turn_credentials_prefix) :].split("/", 1)[0]
        return await _turn_credentials_preflight_response(session_token, origin)

    return None


class PublicEmbedCORSMiddleware:
    """Allow token-gated embed CORS before global SaaS CORS rejects preflights."""

    def __init__(self, app: ASGIApp, api_prefix: str = "/api/v1"):
        self.app = app
        self.api_prefix = api_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        requested_method = headers.get("access-control-request-method")

        if origin and requested_method:
            response = await build_public_embed_preflight_response(
                scope.get("path", ""), origin, requested_method, self.api_prefix
            )
            if response is not None:
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


@router.post("/init", response_model=InitEmbedResponse)
async def initialize_embed_session(
    request: Request, init_request: InitEmbedRequest, response: Response
):
    """Initialize an embed session with token validation and domain checking.

    This endpoint:
    1. Validates the embed token
    2. Checks domain whitelist
    3. Creates a workflow run
    4. Generates a temporary session token
    5. Returns configuration for the widget
    """
    origin = get_request_origin(request)

    # Validate embed token
    embed_token = await db_client.get_embed_token_by_token(init_request.token)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Check if token is active
    if not embed_token.is_active:
        raise HTTPException(status_code=403, detail="Embed token is inactive")

    # Check expiration
    if embed_token.expires_at and embed_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Embed token has expired")

    # Check usage limit
    if embed_token.usage_limit and embed_token.usage_count >= embed_token.usage_limit:
        raise HTTPException(status_code=403, detail="Embed token usage limit exceeded")

    # A share link's minutes for the day. Checked before a run is made, so a
    # spent link costs nothing further; the message names the remedy.
    await _assert_link_has_minutes(embed_token)

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    if origin:
        _allow_embed_origin(response, origin)

    is_text = (init_request.mode or "voice").strip().lower() == "text"
    run_mode = (
        WorkflowRunMode.TEXTCHAT.value if is_text else WorkflowRunMode.SMALLWEBRTC.value
    )

    # Create workflow run
    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"Embed Run - {datetime.now(UTC).isoformat()}",
            workflow_id=embed_token.workflow_id,
            mode=run_mode,
            user_id=embed_token.created_by,  # Use token creator as run owner
            organization_id=embed_token.organization_id,
            initial_context={
                **(init_request.context_variables or {}),
                "provider": run_mode,
            },
        )
    except Exception as e:
        logger.error(f"Failed to create workflow run: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workflow run")

    # Generate session token
    session_token = generate_session_token()

    # Create embed session
    try:
        await db_client.create_embed_session(
            session_token=session_token,
            embed_token_id=embed_token.id,
            workflow_run_id=workflow_run.id,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            origin=origin[:255],
            expires_at=datetime.now(UTC) + timedelta(hours=1),  # 1 hour expiry
        )
    except Exception as e:
        logger.error(f"Failed to create embed session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

    # Increment usage count
    await db_client.increment_embed_token_usage(embed_token.id)

    # A text session needs its transcript and checkpoint to exist before the
    # first message arrives. Done here rather than lazily on first message so a
    # widget that fails to start fails at init, where the failure is visible,
    # rather than swallowing the visitor's opening sentence.
    if is_text:
        try:
            await _start_text_session(workflow_run.id)
        except Exception as e:
            logger.error(f"Failed to start embed text session: {e}")
            raise HTTPException(status_code=500, detail="Failed to start chat session")

    # Prepare configuration
    config = {
        "workflow_id": embed_token.workflow_id,
        "workflow_run_id": workflow_run.id,
        "mode": "text" if is_text else "voice",
        **(embed_token.settings or {}),
    }

    return InitEmbedResponse(
        session_token=session_token, workflow_run_id=workflow_run.id, config=config
    )


@router.options("/config/{token}")
async def options_embed_config(token: str, request: Request):
    """Fallback OPTIONS handler for the embed config endpoint.

    Browser preflights include Access-Control-Request-Method and are handled by
    PublicEmbedCORSMiddleware before global CORS. This keeps non-conformant
    OPTIONS requests on the same validation path.
    """
    return await _config_preflight_response(token, request.headers.get("origin", ""))


async def _assert_link_has_minutes(embed_token) -> None:
    """403 with the reason when a capped token has spent its day."""
    from api.services import share_links

    cap = getattr(embed_token, "daily_minutes_cap", None)
    if cap is None:
        return
    async with db_client.async_session() as session:
        try:
            await share_links.assert_within_cap(
                session, embed_token_id=embed_token.id, cap_minutes=cap
            )
        except share_links.LinkCapReached as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/config/{token}", response_model=EmbedConfigResponse)
async def get_embed_config(token: str, request: Request, response: Response):
    """Get embed configuration without creating a session.

    This endpoint is used to fetch widget configuration for display purposes
    without actually starting a call session.
    """
    origin = get_request_origin(request)

    # Validate embed token
    embed_token = await db_client.get_embed_token_by_token(token)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Check if token is active
    if not embed_token.is_active:
        raise HTTPException(status_code=403, detail="Embed token is inactive")

    # Expiry is enforced on /init already; a config route that ignores it keeps
    # answering for a token that can no longer start a call.
    if embed_token.expires_at and embed_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Embed token has expired")

    # The same for a link that has spent its day: the talk page reads this
    # before it mounts the widget, so the visitor sees a sentence, not a
    # button that fails.
    await _assert_link_has_minutes(embed_token)

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Set CORS header explicitly; the global CORSMiddleware covers only
    # first-party origins; this endpoint is fetched by external embed sites.
    if origin:
        _allow_embed_origin(response, origin)

    # Extract settings with defaults
    settings = embed_token.settings or {}

    # The widget needs something it can put in an <img src>. What is stored is
    # a storage key, which is not that, and must not be handed to a visitor's
    # browser either — it names the object's real location. The public route
    # below is the only address the widget ever sees.
    logo_url = None
    if logo_from_settings(settings):
        logo_url = (
            f"{str(BACKEND_API_ENDPOINT).rstrip('/')}/api/v1/public/embed/logo/{token}"
        )

    agent_name = None
    try:
        agent_name = await db_client.get_workflow_name(
            embed_token.workflow_id, organization_id=embed_token.organization_id
        )
    except Exception:  # noqa: BLE001 - a label, never a reason to refuse
        agent_name = None

    return EmbedConfigResponse(
        workflow_id=embed_token.workflow_id,
        agent_name=agent_name,
        settings=public_settings(settings),
        theme=settings.get("theme", "light"),
        position=settings.get("position", "bottom-right"),
        button_text=settings.get("buttonText", "Start Voice Call"),
        button_color=settings.get("buttonColor", "#3B82F6"),
        size=settings.get("size", "medium"),
        auto_start=settings.get("autoStart", False),
        logo_url=logo_url,
    )


@router.get("/logo/{token}")
async def get_embed_logo(token: str):
    """Serve the widget's logo to whoever the widget is showing itself to.

    Deliberately *not* origin-checked, unlike every other route in this file.
    An <img> does not send an Origin header, so a check here would fail for
    every legitimate visitor while stopping nobody: anyone who can read the
    page can read the token, and the thing behind this URL is a logo the
    customer is already displaying publicly on their own website.

    What it does keep is that the storage key never leaves the server, and
    that an inactive or expired token stops serving.
    """
    embed_token = await db_client.get_embed_token_by_token(token)
    if not embed_token or not embed_token.is_active:
        raise HTTPException(status_code=404, detail="Not found")

    if embed_token.expires_at and embed_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=404, detail="Not found")

    logo = logo_from_settings(embed_token.settings)
    if not logo:
        raise HTTPException(status_code=404, detail="Not found")

    # The key must be one we minted for this tenant and this workflow. Without
    # this, an authenticated tenant could point their own token's settings at
    # another organization's recording and read it through this route, which
    # has no authentication by design. `sanitize_client_settings` stops that
    # being writable at all; this is the second lock, and it also covers any
    # row written before that guard existed.
    if not is_own_logo_key(
        logo.get("key", ""), embed_token.organization_id, embed_token.workflow_id
    ):
        logger.error(
            "Refusing to sign an embed logo key outside its own tenant prefix "
            f"(token organization {embed_token.organization_id})"
        )
        raise HTTPException(status_code=404, detail="Not found")

    backend = logo.get("backend") or get_current_storage_backend().value
    try:
        # `expiration`, not `expires_in`. The wrong keyword raised TypeError,
        # which the except below swallowed as "a missing object", so this route
        # returned 404 for every logo that existed — the feature never worked
        # once, and looked like a storage problem when it was a typo.
        signed_url = await get_storage_for_backend(backend).aget_signed_url(
            logo["key"], expiration=LOGO_URL_TTL_SECONDS
        )
    except TypeError:
        # Not caught. A wrong keyword or arity is a bug in this file, and
        # swallowing it as "missing object" is exactly how the line above
        # returned 404 for every logo that existed. Let it 500 and be seen.
        raise
    except Exception as error:  # noqa: BLE001 - a missing object is a 404, not a 500
        logger.warning(f"Could not sign embed logo {logo['key']}: {error}")
        raise HTTPException(status_code=404, detail="Not found") from error

    if not signed_url:
        raise HTTPException(status_code=404, detail="Not found")

    # Cached for less than the signed URL lives, so a browser never holds a
    # redirect to a URL that has already expired.
    return RedirectResponse(
        url=signed_url,
        status_code=302,
        headers={"Cache-Control": f"public, max-age={LOGO_CACHE_SECONDS}"},
    )


@router.options("/init")
async def options_init(request: Request):
    """Fallback OPTIONS handler for init endpoint."""
    # Browser preflights are handled by PublicEmbedCORSMiddleware before global CORS.
    # For init endpoint, we need to check the token in the request body
    # But OPTIONS requests don't have body, so we'll be permissive
    # The actual validation happens in the POST request
    origin = request.headers.get("origin", "*")

    return _cors_response(origin, "POST, OPTIONS")


@router.get("/turn-credentials/{session_token}", response_model=TurnCredentialsResponse)
async def get_public_turn_credentials(
    session_token: str, request: Request, response: Response
):
    """Get TURN credentials for an embed session.

    This endpoint allows embedded widgets to obtain TURN server credentials
    for WebRTC connections without requiring authentication.

    Args:
        session_token: The session token from embed initialization

    Returns:
        TurnCredentialsResponse with username, password, ttl, and TURN URIs
    """
    origin = get_request_origin(request)

    # Validate session token
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        raise HTTPException(status_code=404, detail="Invalid session token")

    # Check if session is expired
    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Session expired")

    # Get the embed token to check allowed domains
    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Validate domain (a token with no domains is allowed nowhere)
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed for TURN credentials: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    if origin:
        _allow_embed_origin(response, origin)

    # Check if TURN is configured
    if not TURN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )

    try:
        # Use session token as identifier for TURN credentials
        credentials = generate_turn_credentials(f"embed:{session_token[:16]}")
        return TurnCredentialsResponse(**credentials)
    except Exception as e:
        logger.error(f"Failed to generate TURN credentials for embed session: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate TURN credentials",
        )


@router.options("/turn-credentials/{session_token}")
async def options_turn_credentials(request: Request, session_token: str):
    """Fallback OPTIONS handler for TURN credentials endpoint."""
    # Browser preflights are handled by PublicEmbedCORSMiddleware before global CORS.
    return await _turn_credentials_preflight_response(
        session_token, request.headers.get("origin", "")
    )


# ---------------------------------------------------------------------------
# Text chat
# ---------------------------------------------------------------------------
#
# The authenticated text-chat routes under /workflow serve the editor's test
# chat: they require a logged-in user and run against the draft. A visitor on a
# customer's website is neither, so the public surface is these two endpoints —
# same service underneath, embed-token authorisation instead of a session
# cookie, and the published agent rather than the draft.


class EmbedTextMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class EmbedTextMessageResponse(BaseModel):
    #: Everything the agent has said and heard, oldest first. Returned whole
    #: rather than as a delta because the widget is stateless between messages
    #: and a visitor who reloads the page should not lose the conversation.
    messages: list[dict]
    is_completed: bool


async def _start_text_session(run_id: int) -> None:
    from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
    from api.services.workflow.text_chat_session_service import (
        default_text_chat_session_data,
        initialize_text_chat_session,
    )

    text_session = await db_client.ensure_workflow_run_text_session(
        run_id,
        session_data=default_text_chat_session_data(),
        checkpoint=default_text_chat_checkpoint(),
    )
    await initialize_text_chat_session(run_id=run_id, text_session=text_session)


def _visible_messages(session_data: dict) -> list[dict]:
    """The transcript, stripped to what a visitor may see.

    Whitelisted rather than filtered: the session carries tool calls, node
    names and internal reasoning, and a blacklist is one new key away from
    leaking how the agent works to anyone who opens the network tab.
    """
    out = []
    for message in (session_data or {}).get("messages", []) or []:
        role = message.get("role")
        content = message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append({"role": role, "content": content})
    return out


async def _resolve_embed_session(session_token: str, request: Request, response):
    """Session token to embed session, with the same checks the voice path makes."""
    origin = get_request_origin(request)

    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        raise HTTPException(status_code=404, detail="Invalid session token")
    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Session expired")

    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed for embed text: {origin} not in "
            f"{embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    if origin:
        _allow_embed_origin(response, origin)
    return embed_session, embed_token


@router.post("/text/{session_token}/messages", response_model=EmbedTextMessageResponse)
async def post_embed_text_message(
    session_token: str,
    body: EmbedTextMessageRequest,
    request: Request,
    response: Response,
):
    """One turn of a website chat: the visitor's message in, the agent's out."""
    from api.services.workflow.text_chat_session_service import (
        append_text_chat_user_message,
        execute_pending_text_chat_turn,
        normalize_text_chat_session_data,
    )

    embed_session, embed_token = await _resolve_embed_session(
        session_token, request, response
    )
    run_id = embed_session.workflow_run_id

    # Scoped to the token's organization, like every other caller: the loader
    # refuses a run from another tenant, and the embed token is the only proof
    # of which tenant this visitor may talk to.
    text_session = await db_client.get_workflow_run_text_session(
        run_id, organization_id=embed_token.organization_id
    )
    if not text_session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    try:
        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            user_text=body.text,
            expected_revision=None,
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=embed_token.workflow_id,
            run_id=run_id,
            text_session=text_session,
        )
    except Exception as e:
        logger.error(f"Embed text turn failed for run {run_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not send that message")

    session_data = normalize_text_chat_session_data(text_session.session_data)
    return EmbedTextMessageResponse(
        messages=_visible_messages(session_data),
        is_completed=bool(text_session.workflow_run.is_completed),
    )
