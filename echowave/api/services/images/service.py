"""One request for images, start to finish.

1. **Check the brief** (``guard``): every fact on the image must be in what
   the person said. If not, nothing is spent; the model is told what to ask.
2. **Find the provider** (``keys``): the workspace's choice and its key. If
   there is none, the provider card goes on the thread (``offer``) and the
   request waits there.
3. **Generate** once -- never retried -- with any reference image the person
   attached (a logo, a product photo) and, for an edit, the image edited.
4. **Store** each option in the workspace's bucket with its row, its vendor
   cost and a customer charge of zero (``store``, ``metering``).
5. **Show** the grid on the thread: one ``images_made`` row whose card has
   Download and Edit this one under every option.

Callable from Decibyl's thread and from an agent's run alike; the caller
says which workflow (if any) it is, and the rows go on that thread.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from loguru import logger

from api.enums import AgentEventActor, AgentEventKind
from api.services import images
from api.services.images import formats, guard, keys, metering, offer, registry, store
from api.services.images.providers.base import (
    ImageProviderError,
    ImageRequest,
    InputImage,
)
from api.services.workflow import agent_timeline

DEFAULT_OPTIONS = 3
MAX_OPTIONS = 4
MAX_REFERENCES = 3


class ReferenceRefused(ValueError):
    """An attached image could not be taken; the message is for the person."""


async def add_reference(
    *,
    organization_id: int,
    user_id: int | None,
    data: bytes,
    filename: str | None,
) -> dict[str, Any]:
    """Keep a logo or product photo the person attached, for a poster.

    The type is read off the bytes, not the name or the browser's word for
    it, and only PNG, JPEG and WebP are kept -- the three every provider
    here accepts."""
    if not images.enabled(organization_id):
        raise ReferenceRefused("Making images is not switched on here.")
    if not data:
        raise ReferenceRefused("That file is empty.")
    if len(data) > store.MAX_REFERENCE_BYTES:
        raise ReferenceRefused("That image is too large; attach one under 8 MB.")
    from api.services.images.providers.base import image_size, sniff_mime

    mime = sniff_mime(data, fallback="")
    if mime not in store.REFERENCE_TYPES:
        raise ReferenceRefused("Attach a PNG, JPEG or WebP image.")
    width, height = image_size(data)
    row = await store.save(
        organization_id=organization_id,
        data=data,
        mime_type=mime,
        kind=store.REFERENCE,
        user_id=user_id,
        filename=filename,
        width=width,
        height=height,
    )
    return store.view(row)


def is_image_attachment(value: str | None) -> bool:
    return store.is_image_id(value)


async def attachment_for(
    organization_id: int, image_uuid: str, filename: str | None = None
) -> dict[str, Any] | None:
    """A message attachment for one of this workspace's images, or None."""
    if not images.enabled(organization_id):
        return None
    row = await store.get(organization_id, image_uuid)
    if row is None:
        return None
    return {
        "image_uuid": row.image_uuid,
        "filename": filename or row.filename or "image",
        "size_bytes": row.size_bytes,
        "mime_type": row.mime_type,
    }


def _count(arguments: dict[str, Any], editing: bool) -> int:
    try:
        wanted = int(arguments.get("options") or (1 if editing else DEFAULT_OPTIONS))
    except (TypeError, ValueError):
        wanted = DEFAULT_OPTIONS
    return max(1, min(wanted, MAX_OPTIONS))


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:MAX_REFERENCES]


async def generate(
    *,
    organization_id: int,
    arguments: dict[str, Any],
    said: str,
    user_id: int | None = None,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, Any]:
    """Make the images ``arguments`` describe. Returns what the model is told.

    ``said`` is the person's own words in this conversation -- the only
    place a fact on the image may come from."""
    if not images.enabled(organization_id):
        return {
            "status": "unavailable",
            "reason": "Making images is not switched on for this workspace.",
        }

    brief = guard.brief_from(arguments)
    fmt, known_format = formats.resolve(brief.format)
    brief.format = fmt.key
    edit_of = str(arguments.get("edit_image_id") or "").strip()
    instruction = " ".join(str(arguments.get("edit_instruction") or "").split())[:400]

    base_row = None
    grounding = said or ""
    if edit_of:
        base_row = await store.get(organization_id, edit_of)
        if base_row is None or base_row.kind != store.GENERATED:
            return {
                "status": "not_found",
                "reason": f"There is no image {edit_of} in this workspace to edit.",
            }
        if base_row.spec:
            # What the person already approved on the image is theirs; an
            # edit keeps it and may say it again.
            approved = guard.brief_from(base_row.spec)
            grounding = f"{grounding}\n" + "\n".join(approved.texts())
            brief = guard.merged(approved, brief)
        if not arguments.get("format") and base_row.format:
            fmt, known_format = formats.resolve(base_row.format)
            brief.format = fmt.key

    problems = guard.check(brief, said=grounding, edit_instruction=instruction)
    if problems:
        return {
            "status": "needs_facts",
            "missing": problems,
            "note": (
                "Nothing was drawn. A poster may only carry facts the person "
                "gave. Ask them for what is missing below in one short "
                "message, or leave it off the poster -- never guess a price, "
                "date, phone number, address or claim."
            ),
        }

    resolved = await keys.resolve(organization_id)
    if resolved is None:
        return await offer.offer(
            organization_id=organization_id,
            request=said.strip().splitlines()[-1] if said.strip() else "",
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )
    provider = registry.get(resolved.provider)
    assert provider is not None  # resolve only answers with a known provider

    references: list[InputImage] = []
    reference_ids = _ids(arguments.get("reference_image_ids"))
    if reference_ids:
        rows = await store.get_many(organization_id, reference_ids)
        missing = [i for i in reference_ids if i not in {r.image_uuid for r in rows}]
        if missing:
            return {
                "status": "not_found",
                "reason": (
                    f"{', '.join(missing)} is not an image in this workspace. "
                    "Use the image id the attachment line gives."
                ),
            }
        for row in rows:
            data = await store.read_bytes(row)
            if data:
                references.append(InputImage(data=data, mime_type=row.mime_type))
    base_input = None
    if base_row is not None:
        data = await store.read_bytes(base_row)
        if not data:
            return {"status": "error", "error": "The image to edit could not be read."}
        base_input = InputImage(data=data, mime_type=base_row.mime_type)

    count = _count(arguments, editing=base_row is not None)
    prompt = guard.compose(
        brief,
        format_label=fmt.label,
        aspect=fmt.aspect,
        references=len(references),
        edit_instruction=instruction if base_row is not None else "",
    )
    request = ImageRequest(
        prompt=prompt,
        format=fmt,
        count=count,
        references=tuple(references),
        base=base_input,
        avoid=guard.AVOID,
    )
    own_key = resolved.key_source == "byok"
    try:
        result = await provider.generate(
            request, api_key=resolved.api_key, own_key=own_key
        )
    except ImageProviderError as exc:
        logger.info(
            "Image provider {} failed for org {}: {}",
            resolved.provider,
            organization_id,
            exc.kind,
        )
        if exc.kind == "auth" and own_key:
            return await offer.offer(
                organization_id=organization_id,
                reason=exc.message,
                provider=resolved.provider,
                request=said.strip().splitlines()[-1] if said.strip() else "",
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
        return {
            "status": "error",
            "error": exc.message,
            "note": "Say what happened in one line. Nothing was retried.",
        }

    per_image, cost_source = await metering.vendor_paise_per_image(
        resolved.provider, result.model
    )
    request_id = f"req_{uuid4().hex}"
    saved = []
    for index, image in enumerate(result.images):
        try:
            row = await store.save(
                organization_id=organization_id,
                data=image.data,
                mime_type=image.mime_type,
                user_id=user_id,
                workflow_id=workflow_id,
                width=image.width,
                height=image.height,
                provider=resolved.provider,
                model=result.model,
                key_source=resolved.key_source,
                format=fmt.key,
                request_id=request_id,
                option_index=index,
                parent_uuid=base_row.image_uuid if base_row is not None else None,
                spec=brief.as_dict(),
                prompt=prompt,
                # The request's usage on its first image, so a sum over rows
                # counts it once.
                usage=(result.usage or {}) if index == 0 else {"same_request": True},
                vendor_cost_paise=per_image,
                charged_paise=metering.CUSTOMER_PAISE_PER_IMAGE,
                cost_source=cost_source,
            )
        except Exception as exc:
            logger.error(
                "Could not store an image for org {}: {}", organization_id, exc
            )
            continue
        saved.append(row)
    if not saved:
        return {
            "status": "error",
            "error": "The images were made but could not be saved. Try again.",
        }

    info = registry.INFO[resolved.provider]
    noun = "edit" if base_row is not None else "option"
    summary = (
        f"{len(saved)} {noun}{'s' if len(saved) != 1 else ''} for "
        f"{brief.business_name} ({fmt.label})"
    )
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.IMAGES_MADE.value,
        actor=AgentEventActor.AGENT.value,
        summary=summary[:500],
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload={
            "request_id": request_id,
            "images": [store.view(row) for row in saved],
            "provider": resolved.provider,
            "provider_label": info.label,
            "format": fmt.key,
            "format_label": fmt.label,
            "target": fmt.target,
            "brief": brief.as_dict(),
            "edit_of": base_row.image_uuid if base_row is not None else None,
            "note": result.note,
            "author_id": user_id,
        },
        in_channel=False,
    )
    return {
        "status": "made",
        "images": [
            {"option": row.option_index + 1, "image_id": row.image_uuid}
            for row in saved
        ],
        "format": fmt.label
        + ("" if known_format else " (the format asked for was not known)"),
        "note": (
            f"{len(saved)} image{'s are' if len(saved) != 1 else ' is'} on the "
            "thread with Download and Edit this one under each. Say so in one "
            "line, then ask which they like or what to change. To change one, "
            "call make_images again with its image_id as edit_image_id and the "
            "change as edit_instruction." + (f" {result.note}" if result.note else "")
        ),
    }
