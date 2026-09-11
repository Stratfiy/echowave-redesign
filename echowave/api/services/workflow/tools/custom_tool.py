"""Custom tool execution for user-defined HTTP API tools."""

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx
from loguru import logger

from api.db import db_client
from api.utils.credential_auth import resolve_auth_header
from api.utils.template_renderer import render_template
from api.utils.url_security import validate_user_configured_service_url

# Map tool parameter types to JSON schema types
TYPE_MAP = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def tool_to_function_schema(tool: Any) -> Dict[str, Any]:
    """Convert a ToolModel to an LLM function schema.

    Args:
        tool: ToolModel instance with name, description, and definition

    Returns:
        Function schema dict compatible with OpenAI/Anthropic function calling
    """
    definition = tool.definition or {}
    config = definition.get("config", {})
    parameters = config.get("parameters", []) or []
    if (
        definition.get("type") == "transfer_call"
        and config.get("destination_source", "static") != "dynamic"
    ):
        parameters = []
    elif (
        definition.get("type") == "transfer_call"
        and config.get("destination_source", "static") == "dynamic"
    ):
        resolver = config.get("resolver")
        if isinstance(resolver, dict):
            parameters = resolver.get("parameters", []) or []
        else:
            parameters = []

    # Build properties and required list from parameters
    properties = {}
    required = []

    for param in parameters:
        param_name = param.get("name", "")
        param_type = param.get("type", "string")
        param_desc = param.get("description", "")
        param_required = param.get("required", True)

        if not param_name:
            continue

        schema_type = TYPE_MAP.get(param_type, "string")
        if schema_type == "object":
            properties[param_name] = {
                "type": "object",
                "additionalProperties": True,
                "description": param_desc,
            }
        elif schema_type == "array":
            properties[param_name] = {
                "type": "array",
                "items": {},
                "description": param_desc,
            }
        else:
            properties[param_name] = {
                "type": schema_type,
                "description": param_desc,
            }

        if param_required:
            required.append(param_name)

    # If this is an end_call tool with endCallReason enabled, add a required 'reason' parameter
    if definition.get("type") == "end_call" and config.get("endCallReason", False):
        default_description = (
            "The reason for ending the call (e.g., 'voicemail_detected', "
            "'issue_resolved', 'customer_requested')"
        )
        properties["reason"] = {
            "type": "string",
            "description": config.get("endCallReasonDescription")
            or default_description,
        }
        required.append("reason")

    # Sanitize tool name for function name (lowercase, underscores only)
    function_name = re.sub(r"[^a-z0-9_]", "_", tool.name.lower())
    # Remove consecutive underscores and trim
    function_name = re.sub(r"_+", "_", function_name).strip("_")

    return {
        "type": "function",
        "function": {
            "name": function_name,
            "description": tool.description or f"Execute {tool.name} tool",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
        "_tool_uuid": tool.tool_uuid,
    }


def _coerce_parameter_value(value: Any, param_type: str) -> Any:
    """Coerce a rendered preset parameter into the configured JSON type."""

    if value is None:
        return None

    if param_type == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    if param_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value

        rendered = str(value).strip()
        if rendered == "":
            return None

        if re.fullmatch(r"[-+]?\d+", rendered):
            return int(rendered)

        return float(rendered)

    if param_type == "boolean":
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        rendered = str(value).strip().lower()
        if rendered in {"true", "1", "yes", "y", "on"}:
            return True
        if rendered in {"false", "0", "no", "n", "off"}:
            return False

        raise ValueError(f"Cannot convert '{value}' to boolean")

    if param_type == "object":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Cannot convert '{value}' to object") from exc
        if isinstance(value, dict):
            return value
        raise ValueError(f"Cannot convert '{value}' to object")

    if param_type == "array":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Cannot convert '{value}' to array") from exc
        if isinstance(value, list):
            return value
        raise ValueError(f"Cannot convert '{value}' to array")

    return value


def _resolve_preset_parameters(
    config: Dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve fixed/template-backed parameters before executing the HTTP request."""

    preset_parameters = config.get("preset_parameters", []) or []
    if not preset_parameters:
        return {}

    initial_context = dict(call_context_vars or {})
    render_context: Dict[str, Any] = {
        **initial_context,
        "initial_context": initial_context,
        "gathered_context": dict(gathered_context_vars or {}),
    }

    resolved: Dict[str, Any] = {}
    for param in preset_parameters:
        param_name = (param.get("name") or "").strip()
        if not param_name:
            continue

        rendered = render_template(param.get("value_template", ""), render_context)
        if rendered in (None, ""):
            if param.get("required", True):
                raise ValueError(
                    f"Preset parameter '{param_name}' resolved to an empty value"
                )
            continue

        resolved[param_name] = _coerce_parameter_value(
            rendered, param.get("type", "string")
        )

    return resolved


#: `{order_id}` in a tool's URL, and nothing else. Deliberately narrow: a REST
#: path segment is a name, and anything cleverer here becomes a template
#: language an operator has to learn.
_URL_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fill_url_placeholders(
    url: str, arguments: Dict[str, Any]
) -> tuple[str, Dict[str, Any], Optional[str]]:
    """Put arguments into a URL's path, and say which ones were spent.

    Most REST APIs address a thing by path -- Shopify's
    ``/orders/{order_id}/fulfillments.json``, and nearly every other
    ``/resource/{id}`` there is. Without this an operator can only reach the
    endpoints whose arguments fit in a query string or a body, which is a small
    fraction of them, and a URL written the natural way is sent with a literal
    brace in it.

    Returns the filled URL, the arguments that were *not* consumed (so a path
    argument is not also sent as a query parameter or in a body), and an error
    when a placeholder has no argument -- reported rather than requested,
    because an unfilled brace reaches somebody else's server and 404s in a way
    nobody can read.

    Every value is percent-encoded with nothing left safe, so a model that
    supplies ``../../admin`` produces one nonsense path segment rather than a
    traversal. The filled URL is still SSRF-checked afterwards.
    """
    names = _URL_PLACEHOLDER.findall(url)
    if not names:
        return url, arguments, None

    remaining = dict(arguments)
    for name in names:
        if remaining.get(name) is None:
            return (
                url,
                arguments,
                f"The tool's URL needs {name} and it was not supplied.",
            )
        url = url.replace("{" + name + "}", quote(str(remaining.pop(name)), safe=""))
    return url, remaining, None


async def execute_http_tool(
    tool: Any,
    arguments: Dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]] = None,
    gathered_context_vars: Optional[Dict[str, Any]] = None,
    organization_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute an HTTP API tool.

    Args:
        tool: ToolModel instance
        arguments: Arguments passed by the LLM (parameter name -> value)
        call_context_vars: Initial context variables available at runtime
        gathered_context_vars: Variables extracted during the conversation
        organization_id: Organization ID for credential lookup

    Returns:
        Result dict with response data or error
    """
    definition = tool.definition or {}
    config = definition.get("config", {})

    # Get HTTP method and URL
    method = config.get("method", "POST").upper()
    url = config.get("url", "")

    # Get headers from config
    headers = dict(config.get("headers", {}) or {})

    # Add auth header if credential is configured
    credential_uuid = config.get("credential_uuid")
    if credential_uuid and organization_id:
        try:
            credential = await db_client.get_credential_by_uuid(
                credential_uuid, organization_id
            )
            if credential:
                auth_header = await resolve_auth_header(
                    credential, persist=db_client.save_oauth_token_cache
                )
                headers.update(auth_header)
                logger.debug(f"Applied credential '{credential.name}' to tool request")
            else:
                logger.warning(
                    f"Credential {credential_uuid} not found for tool '{tool.name}'"
                )
        except Exception as e:
            logger.error(f"Failed to fetch credential for tool '{tool.name}': {e}")

    # Get timeout
    timeout_ms = config.get("timeout_ms", 5000)
    timeout_seconds = timeout_ms / 1000

    try:
        preset_arguments = _resolve_preset_parameters(
            config, call_context_vars, gathered_context_vars
        )
    except ValueError as e:
        logger.error(f"Custom tool '{tool.name}' preset parameter error: {e}")
        return {"status": "error", "error": str(e)}

    resolved_arguments = {**(arguments or {}), **preset_arguments}

    # Answer from the config, without a network call, when the operator has
    # given this tool a stand-in response.
    #
    # This exists because the most common state of a voice agent is "finished,
    # except the backend it calls does not exist yet". Until it did, such an
    # agent could be built and never demonstrated: the tool reached a URL that
    # refused or did not resolve, the model was handed an error at the one
    # moment the conversation was about to become useful, and the call died in
    # front of whoever was being shown it.
    #
    # Marked `mocked` in the result, and that is the point rather than a
    # detail. The model is told it succeeded, so a mocked booking sounds
    # exactly like a real one -- which is right for a demo and a disaster if
    # anyone downstream cannot tell them apart. The flag rides on the run and
    # is what the transcript, the receipt and any later audit read.
    mock_response = config.get("mock_response")
    if isinstance(mock_response, dict):
        logger.info(
            f"Custom tool '{tool.name}' answered from its mock response; "
            f"{method} {url} was not called"
        )
        return {
            "status": "success",
            "mocked": True,
            "data": mock_response,
        }

    # Path arguments first: they are spent on the URL and must not be sent
    # again as a query parameter or in the body.
    url, resolved_arguments, placeholder_error = fill_url_placeholders(
        url, resolved_arguments
    )
    if placeholder_error:
        logger.warning(f"Custom tool '{tool.name}': {placeholder_error}")
        return {"status": "error", "error": placeholder_error}

    # Build request: JSON body for POST/PUT/PATCH, query params for GET/DELETE
    body = None
    params = None
    if method in ("POST", "PUT", "PATCH"):
        body = resolved_arguments
    elif method in ("GET", "DELETE") and resolved_arguments:
        params = resolved_arguments

    # An HTTP tool's URL is operator-configured, but "operator" is any customer
    # on the platform, and this request leaves our network with the tool's
    # credential attached. Block the private/loopback/link-local/metadata ranges
    # so the tool cannot be turned into a server-side request forgery against
    # our own infrastructure. Fail closed with an error the model reports.
    try:
        validate_user_configured_service_url(url, field_name="Tool URL")
    except ValueError as error:
        logger.warning(f"Custom tool '{tool.name}' URL refused: {error}")
        return {"status": "error", "error": str(error)}

    logger.info(
        f"Executing custom tool '{tool.name}' ({tool.tool_uuid}): {method} {url}"
    )
    if preset_arguments:
        logger.debug(
            f"Resolved preset parameters for '{tool.name}': {list(preset_arguments.keys())}"
        )
    # Field *names* only. The values are whatever the agent collected from the
    # person on the line — a name, a phone number, a complaint — and a debug log
    # is not a lawful place to keep that. `redaction.py` exists because the same
    # data on a run row had to be erasable; a log line is not erasable at all.
    logger.debug(
        f"Request fields for '{tool.name}': body={sorted((body or {}).keys())}, "
        f"params={sorted((params or {}).keys())}"
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body,
                params=params,
            )

            # Try to parse JSON response
            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw_response": response.text}

            # The status code decides, not the absence of an exception.
            #
            # This returned "success" for every response the server managed to
            # send, which meant a 401 from an expired token, a 404 from a wrong
            # datacentre in the URL, and a 500 from the vendor all reached the
            # model as a completed action. The model then tells the caller their
            # appointment is booked. Nothing was booked, the call ends, and the
            # only record is a status code nobody reads inside a "success".
            #
            # A redirect counts as a failure here for the same reason: this
            # client does not follow them, so a 301 carries a body that is not
            # the API's answer. Reporting it plainly gets the URL corrected;
            # calling it success hides it until a customer complains.
            if not 200 <= response.status_code < 300:
                logger.warning(
                    f"Custom tool '{tool.name}' returned HTTP "
                    f"{response.status_code}; reporting failure to the caller"
                )
                return {
                    "status": "error",
                    "status_code": response.status_code,
                    "error": (f"The request failed with HTTP {response.status_code}."),
                    "data": response_data,
                }

            logger.debug(
                f"Custom tool '{tool.name}' completed with status {response.status_code}"
            )
            return {
                "status": "success",
                "status_code": response.status_code,
                "data": response_data,
            }

    except httpx.TimeoutException:
        logger.error(f"Custom tool '{tool.name}' timed out after {timeout_seconds}s")
        return {
            "status": "error",
            "error": f"Request timed out after {timeout_seconds} seconds",
        }
    except httpx.RequestError as e:
        logger.error(f"Custom tool '{tool.name}' request failed: {e}")
        return {
            "status": "error",
            "error": f"Request failed: {str(e)}",
        }
    except Exception as e:
        logger.error(f"Custom tool '{tool.name}' execution failed: {e}")
        return {
            "status": "error",
            "error": f"Tool execution failed: {str(e)}",
        }
