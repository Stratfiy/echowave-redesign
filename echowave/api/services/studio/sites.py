"""Studio sites: the source tree, its builds, its preview and its agents.

Everything the Studio chat's tools and the Studio screen do to a site goes
through here, so the ceilings below hold whichever of them asked.

**Every operation takes the organisation.** A site id from the model or the
browser is never trusted to imply ownership; each read and write is scoped
by ``organization_id`` in the query (``db/site_project_client.py``).

**A build only ever happens in the sandbox.** ``npm install`` runs package
scripts, which is arbitrary code from the internet. There is no local
fallback, unlike Code Mode's: without ``SANDBOX_URL`` a build is refused with
a message that says so.

**A failed build never takes the preview down.** The last good output stays
on the row until a build succeeds, so somebody who asked for a change and got
a compile error still has a working site to look at.
"""

from __future__ import annotations

import base64
import io
import re
import secrets
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from api import constants
from api.db import db_client
from api.db.site_project_models import SiteProjectModel
from api.services.embed_script import generate_embed_script
from api.services.studio import scaffold, themes
from api.services.workflow import bot_triggers

# --- ceilings ---------------------------------------------------------------

MAX_SITES_PER_ORGANIZATION = 50
MAX_FILES = 200
MAX_FILE_BYTES = 300_000
#: Under the sandbox's own 5 MB ceiling on what a build is sent.
MAX_SOURCE_BYTES = 3_000_000
MAX_NAME_CHARS = 120
MAX_PATH_CHARS = 200
#: A build that started this long ago and never finished died with its
#: worker; the site may be built again.
STALE_BUILD_AFTER = timedelta(minutes=20)
BUILD_TIMEOUT_SECONDS = 600
#: What of a build's log the model is shown.
LOG_TO_MODEL_CHARS = 3_000

_PATH_CHARS = re.compile(r"^[A-Za-z0-9._@/+-]+$")
#: Directories that are produced, never written: the build makes them.
_GENERATED_DIRS = ("node_modules/", "dist/", ".git/")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ERROR_LINE = re.compile(
    r"(error|failed|cannot find|could not resolve|unexpected|not found|"
    r"is not exported|enoent|eresolve|e404|✘|✗)",
    re.IGNORECASE,
)


class SiteError(ValueError):
    """Something the caller asked for that cannot be done, said plainly."""


@dataclass
class BuildOutcome:
    status: str
    seconds: float | None
    errors: str
    log_tail: str

    def as_result(self, site: SiteProjectModel) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "seconds": self.seconds,
            "preview_url": preview_url(site) if site.built_at else None,
        }
        if self.status != "succeeded":
            out["errors"] = self.errors or self.log_tail
        return out


# --- paths and files --------------------------------------------------------


def clean_path(raw: str) -> str:
    """A safe relative path inside the site, or :class:`SiteError`."""
    path = (raw or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or len(path) > MAX_PATH_CHARS:
        raise SiteError(f"{raw!r} is not a usable file path.")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SiteError(f"{raw!r} is not a usable file path.")
    if not _PATH_CHARS.match(path):
        raise SiteError(
            f"{raw!r} has characters a file path here cannot use; use letters, "
            "digits, dots, dashes, underscores and slashes."
        )
    if any(path.startswith(prefix) for prefix in _GENERATED_DIRS):
        raise SiteError(f"{path} is produced by the build; it is never written.")
    return path


def _source_bytes(files: dict[str, str]) -> int:
    return sum(len(text.encode("utf-8")) for text in files.values())


def _check_tree(files: dict[str, str]) -> None:
    if len(files) > MAX_FILES:
        raise SiteError(f"A site can have at most {MAX_FILES} files.")
    total = _source_bytes(files)
    if total > MAX_SOURCE_BYTES:
        raise SiteError(
            f"The site's source would be {total // 1000} KB; the limit is "
            f"{MAX_SOURCE_BYTES // 1000} KB. Split less, or remove files."
        )


def merged_files(
    current: dict[str, str],
    writes: list[dict[str, Any]],
    deletes: list[str] | None = None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """``current`` with ``writes`` applied and ``deletes`` removed.

    Returns the new tree and the paths written and deleted. Raises before
    anything is changed, so a batch is applied whole or not at all.
    """
    files = dict(current)
    written: list[str] = []
    for item in writes:
        if not isinstance(item, dict):
            raise SiteError("Each file must be an object with path and content.")
        path = clean_path(str(item.get("path", "")))
        content = item.get("content")
        if not isinstance(content, str):
            raise SiteError(f"{path}: content must be text.")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise SiteError(
                f"{path} is over {MAX_FILE_BYTES // 1000} KB; split it into "
                "smaller modules."
            )
        files[path] = content
        written.append(path)
    removed: list[str] = []
    for raw in deletes or []:
        path = clean_path(str(raw))
        if path in files:
            del files[path]
            removed.append(path)
    if "package.json" not in files:
        raise SiteError("package.json cannot be removed; the build needs it.")
    _check_tree(files)
    return files, written, removed


# --- sites ------------------------------------------------------------------


def preview_url(site: SiteProjectModel) -> str:
    base = constants.SITE_PREVIEW_BASE_URL or str(
        constants.BACKEND_API_ENDPOINT
    ).rstrip("/")
    return f"{base}/api/v1/public/sites/{site.preview_token}/"


def summary(site: SiteProjectModel, *, include_files: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": site.id,
        "name": site.name,
        "framework": site.framework,
        "build_status": site.build_status,
        "built_at": site.built_at.isoformat() if site.built_at else None,
        "build_seconds": site.build_seconds,
        "agent_workflow_ids": list(site.agent_workflow_ids or []),
        "updated_at": site.updated_at.isoformat() if site.updated_at else None,
        # built_at is set only by a build that succeeded, and the output of
        # the last one that did is kept through later failures.
        "preview_url": preview_url(site) if site.built_at else None,
    }
    if include_files:
        files = site.files or {}
        out["files"] = [
            {"path": path, "bytes": len(text.encode("utf-8"))}
            for path, text in sorted(files.items())
        ]
    return out


async def create_site(
    *,
    organization_id: int,
    user_id: int | None,
    name: str,
    framework: str = scaffold.FRAMEWORK_VITE_REACT,
    theme: str = themes.DEFAULT_THEME,
) -> SiteProjectModel:
    name = (name or "").strip()[:MAX_NAME_CHARS] or "My site"
    if framework not in scaffold.FRAMEWORKS:
        raise SiteError(
            f"Unknown framework {framework!r}; Studio builds "
            f"{', '.join(scaffold.FRAMEWORKS)}."
        )
    if theme not in themes.THEMES:
        raise SiteError(
            f"No theme {theme!r}. Choose one of: {', '.join(themes.THEMES)}."
        )
    existing = await db_client.list_site_projects(organization_id)
    if len(existing) >= MAX_SITES_PER_ORGANIZATION:
        raise SiteError(
            f"This workspace already has {MAX_SITES_PER_ORGANIZATION} sites; "
            "delete one before starting another."
        )
    return await db_client.create_site_project(
        organization_id=organization_id,
        created_by_user_id=user_id,
        name=name,
        framework=framework,
        files=scaffold.starter_files(framework, title=name, theme=theme),
        preview_token=secrets.token_urlsafe(24),
    )


async def get_site(site_id: Any, *, organization_id: int) -> SiteProjectModel:
    try:
        site_id = int(site_id)
    except (TypeError, ValueError) as exc:
        raise SiteError(f"{site_id!r} is not a site id.") from exc
    site = await db_client.get_site_project(site_id, organization_id=organization_id)
    if site is None:
        raise SiteError(f"No site {site_id} in this workspace.")
    return site


async def write_files(
    site: SiteProjectModel,
    *,
    organization_id: int,
    writes: list[dict[str, Any]],
    deletes: list[str] | None = None,
) -> dict[str, Any]:
    files, written, removed = merged_files(site.files or {}, writes, deletes)
    await db_client.update_site_project(
        site.id, organization_id=organization_id, files=files
    )
    return {
        "written": written,
        "deleted": removed,
        "file_count": len(files),
        "source_kb": round(_source_bytes(files) / 1000, 1),
    }


def read_file(site: SiteProjectModel, path: str) -> str:
    path = clean_path(path)
    files = site.files or {}
    if path not in files:
        raise SiteError(f"{path} does not exist. Files: {', '.join(sorted(files))}")
    return files[path]


# --- builds -----------------------------------------------------------------


def summarize_log(log: str) -> str:
    """The lines of a build log that say what went wrong.

    Bundlers end a failure with a stack trace through their own internals,
    which is the least useful part; the cause is the lines that name a file
    and an error. Those first, with a line of context each, then the tail.
    """
    lines = [_ANSI.sub("", line).rstrip() for line in (log or "").splitlines()]
    lines = [line for line in lines if line.strip()]
    keep: list[str] = []
    seen: set[int] = set()
    for index, line in enumerate(lines):
        if _ERROR_LINE.search(line) and not line.lstrip().startswith("at "):
            for j in (index, index + 1, index + 2):
                if j < len(lines) and j not in seen:
                    seen.add(j)
                    keep.append(lines[j])
    picked = "\n".join(keep)
    if len(picked) > LOG_TO_MODEL_CHARS:
        picked = picked[:LOG_TO_MODEL_CHARS]
    if not picked:
        picked = "\n".join(lines)[-LOG_TO_MODEL_CHARS:]
    return picked


async def _request_build(site: SiteProjectModel) -> dict[str, Any]:
    if not constants.SANDBOX_URL:
        raise SiteError(
            "Site builds need the sandbox service, and this deployment has "
            "none configured (SANDBOX_URL)."
        )
    async with httpx.AsyncClient(
        base_url=constants.SANDBOX_URL.rstrip("/"),
        headers={"x-sandbox-secret": constants.SANDBOX_SECRET or ""},
        timeout=BUILD_TIMEOUT_SECONDS + 60,
    ) as client:
        response = await client.post(
            "/builds",
            json={
                "files": site.files or {},
                "output_dir": scaffold.OUTPUT_DIR[site.framework],
                "timeout_seconds": BUILD_TIMEOUT_SECONDS,
            },
        )
    if response.status_code == 429:
        raise SiteError(
            "The build machine is busy with other builds. Try again in a minute."
        )
    if response.status_code == 503:
        raise SiteError(
            "Site builds are not switched on in this deployment's sandbox "
            "(SANDBOX_BUILD_NETWORK)."
        )
    if response.status_code >= 400:
        raise SiteError(f"The sandbox refused the build: {response.text[:300]}")
    return response.json()


async def build_site(
    site: SiteProjectModel, *, organization_id: int
) -> tuple[SiteProjectModel, BuildOutcome]:
    """Install and build ``site`` in the sandbox, and record what happened."""
    claimed = await db_client.claim_site_build(
        site.id,
        organization_id=organization_id,
        stale_before=datetime.now(UTC) - STALE_BUILD_AFTER,
    )
    if not claimed:
        raise SiteError("This site is already being built. Wait for that one.")

    try:
        result = await _request_build(site)
    except SiteError as exc:
        await db_client.finish_site_build(
            site.id,
            organization_id=organization_id,
            succeeded=False,
            log=str(exc),
            seconds=None,
            dist=None,
        )
        raise
    except httpx.HTTPError as exc:
        logger.warning("Studio build for site {} could not reach the sandbox", site.id)
        message = f"The sandbox could not be reached: {exc}"
        await db_client.finish_site_build(
            site.id,
            organization_id=organization_id,
            succeeded=False,
            log=message,
            seconds=None,
            dist=None,
        )
        raise SiteError(message) from exc

    log = _ANSI.sub("", result.get("log") or "")
    dist = result.get("files") or {}
    succeeded = (
        result.get("exit_code") == 0 and not result.get("timed_out") and bool(dist)
    )
    if result.get("timed_out"):
        log += f"\nThe build ran past {BUILD_TIMEOUT_SECONDS} seconds and was stopped."
    elif result.get("exit_code") == 0 and not dist:
        log += "\nThe build finished but produced no files."
    seconds = result.get("seconds")
    await db_client.finish_site_build(
        site.id,
        organization_id=organization_id,
        succeeded=succeeded,
        log=log,
        seconds=seconds,
        dist=dist if succeeded else None,
    )
    refreshed = await get_site(site.id, organization_id=organization_id)
    outcome = BuildOutcome(
        status="succeeded" if succeeded else "failed",
        seconds=seconds,
        errors="" if succeeded else summarize_log(log),
        log_tail=log[-LOG_TO_MODEL_CHARS:],
    )
    return refreshed, outcome


# --- agents on the site -----------------------------------------------------


def _clean_domain(raw: str) -> str:
    domain = (raw or "").strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain).split("/")[0]
    if not domain or not re.match(r"^(\*|[a-z0-9.*-]+)(:\d+)?$", domain):
        raise SiteError(f"{raw!r} is not a domain.")
    return domain


async def put_agents_on_site(
    site: SiteProjectModel,
    *,
    organization_id: int,
    user_id: int,
    workflow_ids: list[Any],
    domains: list[str],
) -> dict[str, Any]:
    """Put each agent's widget on the site, for the domains named.

    The embed token is a bearer credential printed into a public page, so it
    is only ever issued for domains a person named -- the same rule the embed
    screen enforces (``routes/workflow_embed.py``). An agent that already has
    a token keeps it, and gains the new domains; its other settings stay.
    """
    if not workflow_ids:
        raise SiteError("Name at least one agent to put on the site.")
    cleaned_domains = list(dict.fromkeys(_clean_domain(d) for d in domains or []))
    if not cleaned_domains:
        raise SiteError(
            "Ask which domain the site will live on (for example "
            "clinic.example.com). The agent's widget only runs on the domains "
            "named, so nobody can copy it onto another site at this "
            "workspace's cost."
        )

    ids: list[int] = []
    names: list[str] = []
    snippets: list[str] = []
    for raw_id in workflow_ids:
        try:
            workflow_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise SiteError(f"{raw_id!r} is not an agent id.") from exc
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if workflow is None:
            raise SiteError(f"No agent {workflow_id} in this workspace.")
        tokens = await db_client.get_embed_tokens_by_workflow(
            workflow_id, organization_id, active_only=False
        )
        if tokens:
            existing = tokens[0]
            merged = list(
                dict.fromkeys([*(existing.allowed_domains or []), *cleaned_domains])
            )
            changes: dict[str, Any] = {"allowed_domains": merged, "is_active": True}
            # A token past its expiry would be "on" and refuse every visitor;
            # putting it on a site is asking for it to work.
            if existing.expires_at is not None and existing.expires_at <= datetime.now(
                UTC
            ):
                changes["expires_at"] = None
            token = await db_client.update_embed_token(
                existing.id, organization_id, **changes
            )
        else:
            token = await db_client.create_embed_token(
                workflow_id=workflow_id,
                organization_id=organization_id,
                created_by=user_id,
                allowed_domains=cleaned_domains,
                settings={"enableText": True},
            )
        ids.append(workflow_id)
        names.append(workflow.name)
        snippets.append(generate_embed_script(token))

    files = dict(site.files or {})
    files["index.html"] = scaffold.set_agents_block(
        files.get("index.html", ""), snippets
    )
    _check_tree(files)
    await db_client.update_site_project(
        site.id,
        organization_id=organization_id,
        files=files,
        agent_workflow_ids=ids,
    )
    return {
        "agents": [{"workflow_id": i, "name": n} for i, n in zip(ids, names)],
        "domains": cleaned_domains,
        "note": (
            "The widgets are in index.html. They connect only on the domains "
            "listed; rebuild the site for the change to show."
        ),
    }


# --- design ------------------------------------------------------------------

#: What the model is asked to check in every review, so a review is a look
#: at named things rather than "looks good".
REVIEW_CHECKLIST = (
    "Hierarchy: one clear headline per screen; the eye knows where to go first.",
    "Spacing: generous and even; nothing cramped, nothing floating alone.",
    "Contrast: every piece of text is easy to read on what it sits on.",
    "Phone: nothing cut off, nothing side by side that should stack, tap targets large.",
    "Content: no placeholder words left ('Service one', 'A short line on...').",
    "Images: every photo relevant, sharp, and not stretched.",
    "Consistency: one button style, one card style, aligned edges.",
)


async def _request_screenshots(dist: dict[str, str]) -> dict[str, Any]:
    if not constants.SANDBOX_URL:
        raise SiteError("Design review needs the sandbox service (SANDBOX_URL).")
    async with httpx.AsyncClient(
        base_url=constants.SANDBOX_URL.rstrip("/"),
        headers={"x-sandbox-secret": constants.SANDBOX_SECRET or ""},
        timeout=180,
    ) as client:
        response = await client.post("/screenshots", json={"files": dist})
    if response.status_code == 429:
        raise SiteError("The sandbox is busy. Try the review again in a minute.")
    if response.status_code >= 400:
        raise SiteError(
            f"The sandbox could not take screenshots: {response.text[:300]}"
        )
    return response.json()


async def review_design(site: SiteProjectModel) -> dict[str, Any]:
    """Screenshots of the last good build, for the model to look at.

    Returned with the pictures under ``IMAGES_KEY``, which the model client
    sends as images (``agent_builder/client.py``) and the transcript drops
    once the turn is over (``studio/session.compact``).
    """
    from api.services.agent_builder.client import IMAGES_KEY

    if not site.dist:
        raise SiteError("Build the site first; there is nothing to look at yet.")
    try:
        shot = await _request_screenshots(site.dist)
    except httpx.HTTPError as exc:
        raise SiteError(f"The sandbox could not be reached: {exc}") from exc
    shots = shot.get("shots") or []
    if not shots:
        raise SiteError(
            "No screenshot came back: " + "; ".join(shot.get("errors") or ["unknown"])
        )
    overflow = shot.get("overflow") or {}
    return {
        "views": [f"{s['name']} {s['width']}x{s['height']}" for s in shots],
        "page_errors": shot.get("errors") or [],
        "scrolls_sideways_by_px": overflow,
        "check": list(REVIEW_CHECKLIST),
        "note": (
            "Look at both pictures against each check. Fix what is wrong in "
            "the files, build, and review again -- at most twice more. Fonts "
            "and photos from the web may be missing in the pictures; that "
            "alone is not a problem."
        ),
        IMAGES_KEY: [
            {"media_type": "image/png", "data": s["png"], "label": s["name"]}
            for s in shots
            if s.get("png")
        ],
    }


async def apply_theme(
    site: SiteProjectModel, *, organization_id: int, theme_name: str
) -> dict[str, Any]:
    try:
        theme = themes.get(theme_name)
    except KeyError as exc:
        raise SiteError(
            f"No theme {theme_name!r}. Choose one of: {', '.join(themes.THEMES)}."
        ) from exc
    files = themes.apply(dict(site.files or {}), theme)
    _check_tree(files)
    await db_client.update_site_project(
        site.id, organization_id=organization_id, files=files
    )
    return {
        "theme": theme.name,
        "fonts": f"{theme.display.family} / {theme.body.family}",
        "colors": theme.colors,
        "note": "Written to src/theme.css and src/fonts.js. Build to see it.",
    }


# --- the contact form, to an agent -------------------------------------------


def form_endpoint(site: SiteProjectModel) -> str:
    base = str(constants.BACKEND_API_ENDPOINT).rstrip("/")
    return f"{base}/api/v1/public/sites/{site.preview_token}/form"


async def connect_form(
    site: SiteProjectModel,
    *,
    organization_id: int,
    user_id: int,
    workflow_id: Any,
    instruction: str,
) -> dict[str, Any]:
    """Make the site's contact form start ``workflow_id`` with each lead.

    The agent is rung through an ordinary bot trigger, so a lead runs exactly
    like any other trigger: the same queue, the same hourly ceiling, the same
    run history. The trigger's secret never reaches the page -- the form
    posts to this site's own address, and the server rings the trigger.
    """
    try:
        workflow_id = int(workflow_id)
    except (TypeError, ValueError) as exc:
        raise SiteError(f"{workflow_id!r} is not an agent id.") from exc
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise SiteError(f"No agent {workflow_id} in this workspace.")
    instruction = (instruction or "").strip()
    if not instruction:
        raise SiteError(
            "Say what the agent should do with each enquiry -- for example "
            "'Send them a WhatsApp confirming we got it and offer three slots'."
        )

    fields = {
        "name": f"Website form: {site.name}"[:120],
        "sentence": f"Someone sends the contact form on the {site.name} website.",
        "instruction": instruction[:4000],
        # The shape schemas/bot_trigger.TriggerField declares. None is
        # required: a visitor may leave a phone and no email, or the other
        # way round, and the agent works with what came.
        "fields": [
            {"name": "name", "description": "Who sent it", "required": False},
            {"name": "phone", "description": "Their phone number", "required": False},
            {"name": "email", "description": "Their email address", "required": False},
            {"name": "message", "description": "What they asked", "required": False},
        ],
        "filter": [],
    }
    existing = None
    if site.form_trigger_id:
        existing = await db_client.get_bot_trigger(
            site.form_trigger_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
        )
    if existing is not None:
        trigger = await db_client.update_bot_trigger(
            existing.id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            is_active=True,
            **fields,
        )
    else:
        current = await db_client.bot_triggers_for_workflow(
            workflow_id, organization_id=organization_id
        )
        if len(current) >= bot_triggers.MAX_PER_WORKFLOW:
            raise SiteError(
                f"Agent {workflow_id} already has {bot_triggers.MAX_PER_WORKFLOW} "
                "triggers; remove one on its Triggers screen first."
            )
        trigger = await db_client.create_bot_trigger(
            organization_id=organization_id,
            workflow_id=workflow_id,
            uuid=bot_triggers.new_uuid(),
            secret=bot_triggers.new_secret(),
            source=bot_triggers.SOURCE_WEBHOOK,
            created_by=user_id,
            **fields,
        )
    files = dict(site.files or {})
    files[scaffold.CONFIG_PATH] = scaffold.config_js(form_endpoint(site))
    await db_client.update_site_project(
        site.id,
        organization_id=organization_id,
        files=files,
        form_trigger_id=trigger.id,
    )
    return {
        "connected": True,
        "agent": workflow.name,
        "form_posts_to": form_endpoint(site),
        "note": (
            "Each enquiry now starts the agent with that instruction. It uses "
            "the tools attached to it -- attach WhatsApp, email or calendar "
            "actions if the instruction needs them. Build the site for the "
            "form to go live."
        ),
    }


#: What a form may carry: a handful of short fields, nothing that looks like
#: a file upload or a pasted book.
MAX_FORM_FIELDS = 12
MAX_FORM_VALUE_CHARS = 2000
HONEYPOT_FIELD = "website"


class FormRejected(ValueError):
    """A submission refused, with the status to answer."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def clean_submission(raw: Any) -> dict[str, str] | None:
    """The fields worth passing on, or None for a bot (the honeypot filled)."""
    if not isinstance(raw, dict):
        raise FormRejected(400, "Send the form's fields as a JSON object.")
    if str(raw.get(HONEYPOT_FIELD) or "").strip():
        return None
    fields: dict[str, str] = {}
    for key, value in raw.items():
        if key == HONEYPOT_FIELD or not isinstance(key, str):
            continue
        if len(fields) >= MAX_FORM_FIELDS:
            break
        name = re.sub(r"[^a-z0-9_]", "", key.lower())[:40]
        text = str(value if value is not None else "").strip()[:MAX_FORM_VALUE_CHARS]
        if name and text:
            fields[name] = text
    if not fields:
        raise FormRejected(400, "The form was empty.")
    return fields


# --- export -----------------------------------------------------------------


def export_zip(site: SiteProjectModel) -> bytes:
    """The source, and the last good build under ``dist/``, as one zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in sorted((site.files or {}).items()):
            archive.writestr(path, text)
        for path, encoded in sorted((site.dist or {}).items()):
            archive.writestr(f"dist/{path}", base64.b64decode(encoded))
    return buffer.getvalue()
