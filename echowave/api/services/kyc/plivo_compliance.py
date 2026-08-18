"""HTTP client for Plivo's phone-number Compliance API.

Plivo's India numbers cannot be bought, let alone dialled from, until the end
customer has an *accepted* compliance application on file. This module is the
transport for that: it knows the endpoint shapes, the multipart encoding, the
document constraints and the rate limits, and nothing about our own KYC state
machine. The adapter that joins the two lives in :mod:`api.services.kyc.carrier`.

Kept separate from ``carrier.py`` because the two change for different reasons.
The endpoints and encodings here move when Plivo changes its API; the state
mapping there moves when our own flow changes. Folding them together produced
a module where a rate-limit fix and a compliance-policy fix touched the same
function.

Reference: https://www.plivo.com/docs/numbers/compliance
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import aiohttp
from loguru import logger

#: Plivo's own ceiling, and lower than the 10MB our object store would take.
#: Enforced here as well as at upload because a document that arrives by any
#: other route still cannot be forwarded.
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024

#: Plivo truncates or rejects beyond this. Ours are generated names, but a
#: customer-supplied one reaches this function on the resubmit path.
MAX_FILENAME_CHARS = 99

#: The only types Plivo accepts. Deliberately the same set the object store
#: allows, so nothing can be stored that cannot later be forwarded.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
    }
)

_EXTENSION_BY_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

#: Documented ceilings, per minute. Exceeding them earns a 429, and a 429 on a
#: compliance submission during a customer's signup is worth avoiding rather
#: than retrying through.
REQUIREMENTS_CALLS_PER_MINUTE = 100
COMPLIANCE_CALLS_PER_MINUTE = 60

#: How long a requirements response stays usable. The document-type ids in it
#: are stable for weeks at a time, and re-fetching them on every submission
#: would spend the tighter of the two rate limits on data that did not change.
REQUIREMENTS_CACHE_SECONDS = 3600


class PlivoComplianceError(RuntimeError):
    """Plivo refused a compliance request.

    Carries the status and body because the body is where Plivo says *which*
    field it disliked, and that sentence is the one a customer needs to see.
    """

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class PlivoRateLimited(PlivoComplianceError):
    """A 429, or a local bucket that would have caused one.

    Separate from the general error so a caller can retry this and only this.
    """

    def __init__(self, message: str, *, retry_after: float = 1.0):
        super().__init__(message, status=429)
        self.retry_after = retry_after


class DocumentTooLarge(PlivoComplianceError):
    """A document exceeds Plivo's 5MB ceiling."""


@dataclass(frozen=True)
class ComplianceDocument:
    """One file destined for a compliance application.

    ``document_type_id`` comes from :meth:`PlivoComplianceClient.requirements`
    and is never hardcoded — the ids in Plivo's documentation are examples, and
    submitting one of those would attach the document under the wrong type.
    """

    document_type_id: str
    filename: str
    content: bytes
    content_type: str
    #: Extra fields Plivo requires *for this document type*, merged into the
    #: document's entry in the ``data`` payload. Which fields those are is per
    #: type and per country — an Indian business registration document wants
    #: ``business_name``, and Plivo refuses the whole application without it:
    #: "Field 'business_name' is required for document type '<uuid>'. Check
    #: GET /Requirements for required fields."
    #:
    #: A free-form mapping rather than named fields because the set differs by
    #: document type and Plivo adds to it; the caller decides what a given type
    #: needs and this carries it through unchanged.
    meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise unless Plivo would accept this file.

        Checked before the request rather than after the rejection, because a
        rejected application costs the customer a round trip through our
        review queue to fix.
        """
        normalized = (self.content_type or "").split(";")[0].strip().lower()
        if normalized not in ALLOWED_CONTENT_TYPES:
            raise PlivoComplianceError(
                f"{self.filename!r} is a {normalized or 'unknown'} file. "
                "Plivo accepts PDF, JPEG and PNG only."
            )
        if not self.content:
            raise PlivoComplianceError(f"{self.filename!r} is empty.")
        if len(self.content) > MAX_DOCUMENT_BYTES:
            raise DocumentTooLarge(
                f"{self.filename!r} is "
                f"{len(self.content) / (1024 * 1024):.1f}MB. Plivo's limit is "
                f"{MAX_DOCUMENT_BYTES // (1024 * 1024)}MB."
            )
        if not self.document_type_id:
            raise PlivoComplianceError(
                f"{self.filename!r} has no document type id. Fetch the "
                "requirements for this country and number type first."
            )

    def safe_filename(self) -> str:
        """The filename Plivo will see, within its length limit.

        Truncated from the *stem* so the extension survives — Plivo infers
        nothing from the extension, but a reviewer opening the file does.
        """
        name = (self.filename or "document").strip() or "document"
        if len(name) <= MAX_FILENAME_CHARS:
            return name

        suffix = PurePosixPath(name).suffix
        if not suffix or len(suffix) > 12:
            suffix = _EXTENSION_BY_TYPE.get(
                (self.content_type or "").split(";")[0].strip().lower(), ""
            )
        stem = name[: MAX_FILENAME_CHARS - len(suffix)]
        return f"{stem}{suffix}"


class _RateLimiter:
    """A per-minute call budget, shared by every caller of one client.

    A sliding window rather than a token bucket because the published limits
    are stated per minute, and matching the shape of the documented limit is
    what keeps this honest at the boundary.
    """

    def __init__(self, calls_per_minute: int):
        self._budget = calls_per_minute
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        #: Set from a 429 or an exhausted ``X-RateLimit-Remaining``. Until this
        #: passes, every caller waits — one thread learning it is limited is
        #: worth the rest not finding out the same way.
        self._blocked_until = 0.0

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()

                wait = 0.0
                if now < self._blocked_until:
                    wait = self._blocked_until - now
                elif len(self._calls) >= self._budget:
                    wait = 60.0 - (now - self._calls[0])

                if wait <= 0:
                    self._calls.append(now)
                    return

            logger.debug("Plivo compliance rate limit: waiting {:.1f}s", wait)
            await asyncio.sleep(min(wait, 60.0))

    def observe(self, headers) -> None:
        """Back off from what the response says, not only from what we counted.

        Our own window can drift from Plivo's — other processes share the
        account — so the headers are the authority whenever they disagree.
        """
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        try:
            if remaining is not None and int(remaining) <= 0:
                self._blocked_until = time.monotonic() + max(1.0, float(reset or 60.0))
        except (TypeError, ValueError):
            # A header we cannot parse is not a reason to fail a request that
            # otherwise succeeded.
            pass

    def block_for(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)


class PlivoComplianceClient:
    """Talks to one Plivo account's compliance endpoints.

    One instance per account, because the rate limiters are per account and
    sharing them across accounts would make a busy customer throttle a quiet
    one.
    """

    def __init__(
        self,
        auth_id: str,
        auth_token: str,
        *,
        base_url: str = "https://api.plivo.com/v1",
        timeout_seconds: float = 30.0,
    ):
        if not auth_id or not auth_token:
            raise PlivoComplianceError("Plivo auth_id and auth_token are required.")
        self.auth_id = auth_id
        self._auth = aiohttp.BasicAuth(auth_id, auth_token)
        self._root = f"{base_url.rstrip('/')}/Account/{auth_id}/PhoneNumber/Compliance"
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._requirements_limiter = _RateLimiter(REQUIREMENTS_CALLS_PER_MINUTE)
        self._compliance_limiter = _RateLimiter(COMPLIANCE_CALLS_PER_MINUTE)
        self._requirements_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}

    # -- transport ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        limiter: _RateLimiter,
        params: dict | None = None,
        json_body: dict | None = None,
        form: aiohttp.FormData | None = None,
    ) -> dict[str, Any]:
        await limiter.acquire()
        url = f"{self._root}{path}"

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.request(
                    method,
                    url,
                    auth=self._auth,
                    params=params,
                    json=json_body,
                    data=form,
                ) as response:
                    body = await response.text()
                    limiter.observe(response.headers)

                    if response.status == 429:
                        retry_after = float(response.headers.get("Retry-After") or 5.0)
                        limiter.block_for(retry_after)
                        raise PlivoRateLimited(
                            f"Plivo rate limited {method} {path}",
                            retry_after=retry_after,
                        )

                    if response.status >= 400:
                        raise PlivoComplianceError(
                            f"Plivo compliance {method} {path} failed with "
                            f"{response.status}: {body[:500]}",
                            status=response.status,
                            body=body,
                        )

                    if not body:
                        return {}
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        raise PlivoComplianceError(
                            f"Plivo returned non-JSON for {method} {path}: "
                            f"{body[:200]}",
                            status=response.status,
                            body=body,
                        )
        except aiohttp.ClientError as exc:
            raise PlivoComplianceError(
                f"Could not reach Plivo compliance API: {exc}"
            ) from exc

    # -- requirements ------------------------------------------------------

    async def requirements(
        self,
        *,
        country_iso: str = "IN",
        number_type: str = "local",
        user_type: str = "business",
    ) -> dict[str, Any]:
        """What Plivo needs for this country / number type / end-user type.

        Cached for :data:`REQUIREMENTS_CACHE_SECONDS`. The response carries the
        ``document_type_id`` values a submission must quote, which is the whole
        reason to call it — see :meth:`document_type_ids`.
        """
        key = (country_iso, number_type, user_type)
        cached = self._requirements_cache.get(key)
        if cached and time.monotonic() - cached[0] < REQUIREMENTS_CACHE_SECONDS:
            return cached[1]

        payload = await self._request(
            "GET",
            "/Requirements",
            limiter=self._requirements_limiter,
            params={
                "country_iso": country_iso,
                "number_type": number_type,
                "user_type": user_type,
            },
        )
        self._requirements_cache[key] = (time.monotonic(), payload)
        return payload

    async def document_type_ids(
        self,
        *,
        country_iso: str = "IN",
        number_type: str = "local",
        user_type: str = "business",
    ) -> dict[str, str]:
        """Map ``document_name`` → ``document_type_id`` for this combination.

        Plivo's documentation shows concrete UUIDs in its examples. Those are
        illustrative; an account that submits them attaches its documents under
        types Plivo did not ask for, and the application is rejected with a
        message that does not say so. Hence: always resolved, never literal.
        """
        payload = await self.requirements(
            country_iso=country_iso, number_type=number_type, user_type=user_type
        )
        found: dict[str, str] = {}
        for requirement in _iter_requirements(payload):
            name = requirement.get("document_name") or requirement.get("name")
            type_id = requirement.get("document_type_id") or requirement.get("id")
            if name and type_id:
                found[str(name)] = str(type_id)
        if not found:
            raise PlivoComplianceError(
                "Plivo returned no document types for "
                f"{country_iso}/{number_type}/{user_type}. Cannot build a "
                "submission without them."
            )
        return found

    # -- applications ------------------------------------------------------

    async def create_application(
        self,
        *,
        alias: str,
        country_iso: str,
        number_type: str,
        end_user: dict[str, Any],
        documents: list[ComplianceDocument],
        callback_url: str,
        callback_method: str = "POST",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit a new compliance application.

        Multipart, with a single ``data`` part holding the JSON body and one
        part per file. Every document is validated first — a 5MB ceiling
        discovered by a 400 response mid-submission leaves a half-built
        application on Plivo's side.
        """
        if not callback_url.lower().startswith("https://"):
            raise PlivoComplianceError(
                "Plivo requires an HTTPS callback_url for compliance status "
                f"changes; got {callback_url!r}."
            )

        form = self._build_form(
            data={
                "alias": alias,
                "country_iso": country_iso,
                "number_type": number_type,
                "end_user": end_user,
                "callback_url": callback_url,
                "callback_method": callback_method,
                **(extra or {}),
            },
            documents=documents,
        )
        return await self._request(
            "POST", "/", limiter=self._compliance_limiter, form=form
        )

    async def update_application(
        self,
        compliance_id: str,
        *,
        alias: str | None = None,
        end_user: dict[str, Any] | None = None,
        documents: list[ComplianceDocument],
        callback_url: str | None = None,
        callback_method: str = "POST",
    ) -> dict[str, Any]:
        """Amend a rejected application.

        **``documents`` replaces the whole set.** Plivo does not merge — sending
        only the corrected file drops the others and the application is
        rejected a second time for documents the customer already supplied.
        The parameter is therefore required and not optional, so no caller can
        express the wrong thing by omission.
        """
        if not documents:
            raise PlivoComplianceError(
                "An update replaces every document on the application, so the "
                "full set has to be sent — not only the corrected one."
            )

        data: dict[str, Any] = {}
        if alias is not None:
            data["alias"] = alias
        if end_user is not None:
            data["end_user"] = end_user
        if callback_url is not None:
            if not callback_url.lower().startswith("https://"):
                raise PlivoComplianceError(
                    f"Plivo requires an HTTPS callback_url; got {callback_url!r}."
                )
            data["callback_url"] = callback_url
            data["callback_method"] = callback_method

        form = self._build_form(data=data, documents=documents)
        return await self._request(
            "PATCH",
            f"/{compliance_id}",
            limiter=self._compliance_limiter,
            form=form,
        )

    async def get_application(self, compliance_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/{compliance_id}", limiter=self._compliance_limiter
        )

    async def link(
        self, *, number: str, compliance_application_id: str
    ) -> dict[str, Any]:
        """Attach an accepted application to a number we have already bought.

        Plivo will not link an application that is not ``accepted``, and will
        not link a number the account does not own — which is why provisioning
        buys first and links second.
        """
        return await self._request(
            "POST",
            "/Link/",
            limiter=self._compliance_limiter,
            json_body={
                "numbers": [
                    {
                        "number": number,
                        "compliance_application_id": compliance_application_id,
                    }
                ]
            },
        )

    # -- encoding ----------------------------------------------------------

    def _build_form(
        self, *, data: dict[str, Any], documents: list[ComplianceDocument]
    ) -> aiohttp.FormData:
        """Encode the multipart body Plivo expects.

        The JSON goes in one ``data`` field as a *string*, not as nested form
        fields — Plivo parses that field itself. Files are indexed parts named
        ``documents[n].file``, and the same index in ``data["documents"]``
        carries that file's type id, so the two lists are positional and must
        stay in step. Building both from one loop is what keeps them in step.
        """
        for document in documents:
            document.validate()

        payload = dict(data)
        payload["documents"] = [
            {
                "document_type_id": d.document_type_id,
                "file_name": d.safe_filename(),
                **d.meta,
            }
            for d in documents
        ]

        form = aiohttp.FormData()
        form.add_field("data", json.dumps(payload), content_type="application/json")
        for index, document in enumerate(documents):
            form.add_field(
                f"documents[{index}].file",
                document.content,
                filename=document.safe_filename(),
                content_type=document.content_type,
            )
        return form


def _iter_requirements(payload: dict[str, Any]):
    """Yield requirement entries from whichever shape Plivo returned.

    The endpoint has returned the list under more than one key across versions,
    and a client that understands only one of them fails with "no document
    types" — a message that sends someone looking at their account rather than
    at this parser.
    """
    for key in ("document_types", "documents", "requirements", "objects"):
        entries = payload.get(key)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    yield entry

    # Some responses nest the per-user-type detail one level down.
    for value in payload.values():
        if isinstance(value, dict):
            for key in ("document_types", "documents", "requirements"):
                entries = value.get(key)
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict):
                            yield entry
