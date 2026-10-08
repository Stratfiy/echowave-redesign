"""Template rendering utility with support for nested JSON paths."""

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional, Union
from zoneinfo import ZoneInfo

from loguru import logger

from api.services.workflow.workflow_graph import TEMPLATE_VAR_PATTERN

_CURRENT_TIME_PREFIX = "current_time"
_CURRENT_WEEKDAY_PREFIX = "current_weekday"
_INITIAL_CONTEXT_PREFIX = "initial_context."


def get_nested_value(obj: Any, path: str) -> Any:
    """
    Get a nested value from a dictionary using dot notation.

    Args:
        obj: The object to traverse (dict or any)
        path: Dot-separated path (e.g., "a.b.c")

    Returns:
        The value at the path, or None if not found

    Examples:
        get_nested_value({"a": {"b": 1}}, "a.b") -> 1
        get_nested_value({"a": {"b": {"c": 2}}}, "a.b.c") -> 2
        get_nested_value({"a": 1}, "a.b") -> None
    """
    if not path:
        return obj

    keys = path.split(".")
    current = obj

    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None

        if current is None:
            return None

    return current


def render_template(
    template: Union[str, dict, list, None],
    context: Dict[str, Any],
) -> Union[str, dict, list, None]:  # noqa: C901 – complex but self-contained
    """
    Render a template with variable substitution supporting nested paths.

    Supports:
    - String templates: "Hello {{name}}"
    - JSON templates: {"key": "{{value}}"}
    - Nested paths: "{{initial_context.phone_number}}"
    - Deep nesting: "{{gathered_context.customer.address.city}}"
    - Fallback: "{{name | fallback:Unknown}}"

    Args:
        template: String, dict, list, or None with {{variable}} placeholders
        context: Dict containing all available variables

    Returns:
        Rendered template with variables replaced
    """
    if template is None:
        return None

    # Handle dict templates recursively
    if isinstance(template, dict):
        return {
            _render_string(str(k), context)
            if isinstance(k, str)
            else k: render_template(v, context)
            for k, v in template.items()
        }

    # Handle list templates recursively
    if isinstance(template, list):
        return [render_template(item, context) for item in template]

    # Handle non-string types (int, float, bool, etc.)
    if not isinstance(template, str):
        return template

    return _render_string(template, context)


def _extract_timezone_from_template(template_str: str) -> Optional[str]:
    """Extract the timezone from a ``current_time_<TZ>`` or ``current_weekday_<TZ>`` variable.

    Returns the first IANA timezone found, or None.
    """
    pattern = (
        r"\{\{\s*(?:"
        + re.escape(_CURRENT_TIME_PREFIX)
        + r"|"
        + re.escape(_CURRENT_WEEKDAY_PREFIX)
        + r")_([^|\s}]+)"
    )
    match = re.search(pattern, template_str)
    return match.group(1).strip() if match else None


def _resolve_builtin_variable(
    variable_path: str, default_tz: Optional[str] = None
) -> Optional[str]:
    """Resolve built-in template variables that are available in all contexts.

    Supported variables:
        - ``current_time`` – current time in UTC
        - ``current_time_<TIMEZONE>`` – current time in the given IANA timezone
        - ``current_weekday`` – current weekday name (uses *default_tz* if set, else UTC)
        - ``current_weekday_<TIMEZONE>`` – current weekday name in the given timezone

    Args:
        variable_path: The template variable name to resolve.
        default_tz: Fallback timezone for ``current_weekday`` when no explicit
            timezone suffix is provided (typically inferred from a
            ``current_time_<TZ>`` variable in the same template).

    Returns:
        The resolved string value, or None if *variable_path* is not a
        recognised built-in.
    """
    if variable_path == _CURRENT_TIME_PREFIX:
        tz = ZoneInfo(default_tz) if default_tz else ZoneInfo("UTC")
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    if variable_path.startswith(_CURRENT_TIME_PREFIX + "_"):
        timezone = variable_path[len(_CURRENT_TIME_PREFIX) + 1 :]
        try:
            return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            logger.warning(f"Invalid timezone in template variable: {timezone}")
            return None

    if variable_path == _CURRENT_WEEKDAY_PREFIX:
        tz = ZoneInfo(default_tz) if default_tz else ZoneInfo("UTC")
        return datetime.now(tz).strftime("%A")

    if variable_path.startswith(_CURRENT_WEEKDAY_PREFIX + "_"):
        timezone = variable_path[len(_CURRENT_WEEKDAY_PREFIX) + 1 :]
        try:
            return datetime.now(ZoneInfo(timezone)).strftime("%A")
        except Exception:
            logger.warning(f"Invalid timezone in template variable: {timezone}")
            return None

    return None


#: Stands in for a placeholder that rendered to nothing, so the spoken renderer
#: can see where the hole is and close it. A control character no greeting
#: contains; removed in every path before the text leaves this module.
_EMPTY_MARK = "\x00"

# A hole and whatever introduced it: "Namaste, {{clinic_name}}." loses the
# ", " with the name, and "Hello {{name}}!" loses the space.
_HOLE_WITH_LEAD = re.compile(r"(?:[ \t]*,[ \t]*|[ \t]+)" + _EMPTY_MARK)
# A hole that opens the text (or a line) takes the comma after it instead:
# "{{name}}, welcome" is "Welcome"-shaped, not ", welcome".
_HOLE_AT_START = re.compile(
    r"(^|\n)[ \t]*" + _EMPTY_MARK + r"[ \t]*,?[ \t]*", re.MULTILINE
)
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.!?;:])")
_DOUBLE_SPACE = re.compile(r"[ \t]{2,}")


def render_spoken_template(template: str | None, context: dict[str, Any]) -> str | None:
    """Render text that will be read aloud, closing the hole an unset variable leaves.

    ``render_template`` turns an unset ``{{clinic_name}}`` into nothing, which
    is right for a prompt -- the model reads around it -- and wrong for a
    greeting. Run 23 on staging opened with "Namaste, . How may I help you
    today?": the voice read the stranded comma and full stop as a pause where
    a name should have been, and that is what the caller heard first.

    So here an empty placeholder takes the comma or space that introduced it
    with it: "Namaste, {{clinic_name}}. How may I help you today?" becomes
    "Namaste. How may I help you today?". A placeholder with a value, and text
    with no empty placeholder at all, renders exactly as ``render_template``
    renders it.

    Spoken text only. A prompt keeps ``render_template``, because a model is
    better served seeing "Customer: " with nothing after it than not knowing
    the line was ever there.
    """
    if template is None:
        return None
    if not isinstance(template, str):
        return render_template(template, context)
    rendered = _render_string(template, context, empty=_EMPTY_MARK)
    if _EMPTY_MARK not in rendered:
        return rendered
    spoken = _HOLE_AT_START.sub(r"\1", rendered)
    spoken = _HOLE_WITH_LEAD.sub("", spoken)
    spoken = spoken.replace(_EMPTY_MARK, "")
    spoken = _SPACE_BEFORE_PUNCT.sub(r"\1", spoken)
    spoken = _DOUBLE_SPACE.sub(" ", spoken)
    # A hole at the very start leaves the next word lower-case
    # ("{{name}}, welcome back" -> "welcome back"); a voice does not care, but
    # the same text is shown on the transcript.
    spoken = spoken.strip()
    if rendered.lstrip().startswith(_EMPTY_MARK) and spoken[:1].islower():
        spoken = spoken[0].upper() + spoken[1:]
    return spoken


def _render_string(
    template_str: str, context: dict[str, Any], *, empty: str = ""
) -> str:
    """
    Render a string template with variable substitution.

    Args:
        template_str: String with {{variable}} placeholders
        context: Dict containing all available variables
        empty: What a placeholder with no value (and no fallback) becomes.
            Nothing, except for the spoken renderer, which marks the spot.

    Returns:
        Rendered string with variables replaced
    """
    if not template_str:
        return template_str

    # Pre-scan for a current_time_<TZ> variable so that {{current_weekday}}
    # can inherit the same timezone instead of defaulting to UTC.
    default_tz = _extract_timezone_from_template(template_str)

    def _replace(match: re.Match[str]) -> str:  # type: ignore[type-arg]
        variable_path = match.group(1).strip()
        filter_name = match.group(2).strip() if match.group(2) else None
        filter_value = match.group(3).strip() if match.group(3) else None

        # Check for built-in variables first (current_time, current_weekday)
        builtin_value = _resolve_builtin_variable(variable_path, default_tz)
        if builtin_value is not None:
            return builtin_value

        # Get value using nested path lookup. Prompts commonly reference
        # initial_context.<key>, while some runtime callers pass the initial
        # context itself as the render context.
        value = get_nested_value(context, variable_path)
        if value is None and variable_path.startswith(_INITIAL_CONTEXT_PREFIX):
            value = get_nested_value(
                context, variable_path[len(_INITIAL_CONTEXT_PREFIX) :]
            )

        # Apply fallback: new syntax {{var | default}} or legacy {{var | fallback:default}}
        if filter_name is not None:
            if value is None or value == "":
                if filter_name == "fallback":
                    # Legacy syntax: {{var | fallback:default}}
                    value = (
                        filter_value
                        if filter_value is not None
                        else variable_path.title()
                    )
                else:
                    # New syntax: {{var | default}}
                    value = filter_name

        # Convert to string for substitution
        if value is None:
            return empty
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        text = str(value)
        if empty and not text.strip():
            return empty
        return text

    # Replace template variables
    result = re.sub(TEMPLATE_VAR_PATTERN, _replace, template_str)

    # Handle line breaks (convert literal \n to actual newlines)
    result = result.replace("\\n", "\n")

    return result
