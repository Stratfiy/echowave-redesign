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
from loguru import logger
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.db import db_client
from api.enums import WorkflowRunMode
from api.routes.turn_credentials import (
    TURN_SECRET,
    TurnCredentialsResponse,
    generate_turn_credentials,
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


def validate_origin(origin: str, allowed_domains: list) -> bool:
    """Validate if the origin is in the allowed domains list.

    Args:
        origin: The origin header from the request
        allowed_domains: List of allowed domain patterns

    Returns:
        True if origin is allowed, False otherwise
    """
    if not allowed_domains:
        # If no domains specified, allow all origins
        return True

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

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Set CORS header explicitly; the global CORSMiddleware covers only
    # first-party origins; this endpoint is fetched by external embed sites.
    if origin:
        _allow_embed_origin(response, origin)

    # Extract settings with defaults
    settings = embed_token.settings or {}

    return EmbedConfigResponse(
        workflow_id=embed_token.workflow_id,
        settings=settings,
        theme=settings.get("theme", "light"),
        position=settings.get("position", "bottom-right"),
        button_text=settings.get("buttonText", "Start Voice Call"),
        button_color=settings.get("buttonColor", "#3B82F6"),
        size=settings.get("size", "medium"),
        auto_start=settings.get("autoStart", False),
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

    # Validate domain (empty allowed_domains means allow all)
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

    The stored shape is a list of *turns*, each carrying at most one user
    message and one assistant message alongside node transitions, tool events,
    per-turn token usage and cost, and a checkpoint of the whole graph state.
    Two fields of that are the conversation; the rest is ours.

    So this projects rather than filters. A blacklist over a structure that
    rich is one new key away from publishing how the agent works to anyone who
    opens the network tab — and the keys are added by code that has no reason
    to think about this function.
    """
    out = []
    for turn in (session_data or {}).get("turns") or []:
        if not isinstance(turn, dict):
            continue
        # User first: within a turn the visitor spoke before the agent
        # answered, and a turn can carry either alone — a pending turn has no
        # reply yet, and the opening greeting has no question.
        for role, key in (("user", "user_message"), ("assistant", "assistant_message")):
            message = turn.get(key)
            text = (message or {}).get("text") if isinstance(message, dict) else None
            if isinstance(text, str) and text:
                out.append({"role": role, "content": text})
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

    # Scoped to the token's organization. The session token already proves the
    # visitor owns this run, but the lookup is org-scoped by contract and a
    # positional-only call is what made this a 500 rather than a 404.
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
