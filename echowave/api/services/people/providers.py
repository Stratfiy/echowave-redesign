"""Reading contacts from Google and Microsoft, a page at a time.

Through the person's **own** connection only (their Composio tenant under
``connections_per_person``), never the workspace's: a workspace mailbox's
address book is not any one person's contacts. Composio holds the tokens;
we ask its proxy to make one GET against the provider's API with the
person's connected account, so no token ever reaches this code.

* Google: People API ``people/me/connections`` with ``requestSyncToken``;
  the next sync sends ``syncToken`` and receives only changes, deletions
  flagged ``metadata.deleted``. An expired token (HTTP 410) means a full
  read again, which ``sync`` does by itself.
* Microsoft: Graph ``me/contacts/delta``; ``@odata.nextLink`` pages,
  ``@odata.deltaLink`` is the cursor, removals carry ``@removed``.

Needs the contacts permission on the connection: ``contacts.readonly`` on
Google's (a sensitive scope: Google's OAuth verification) and
``Contacts.Read`` on Microsoft's. Without it the provider answers 403 and
the sync says so in those words.

``PEOPLE_FAKE_PROVIDER_URL`` (local and tests only) sends the same requests
to a fake that answers like both APIs (``api/tests/support/people_fakes.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from api import constants
from api.services.people.normalise import Incoming

GOOGLE = "google"
MICROSOFT = "microsoft"
PROVIDERS = (GOOGLE, MICROSOFT)
NAMES = {GOOGLE: "Google Contacts", MICROSOFT: "Outlook contacts"}

GOOGLE_URL = "https://people.googleapis.com/v1/people/me/connections"
GOOGLE_FIELDS = "names,emailAddresses,phoneNumbers,organizations,metadata"
GRAPH_URL = "https://graph.microsoft.com/v1.0/me/contacts/delta"
GRAPH_SELECT = (
    "displayName,givenName,surname,emailAddresses,mobilePhone,businessPhones,"
    "homePhones,companyName,jobTitle"
)
FAKE_ENVIRONMENTS = ("local", "dev", "test")
TIMEOUT = 30.0


class ProviderError(RuntimeError):
    """The provider could not be read. ``code`` is what the screen shows."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CursorExpired(ProviderError):
    def __init__(self) -> None:
        super().__init__("cursor_expired", "The saved sync point expired.")


@dataclass
class Page:
    contacts: list[Incoming] = field(default_factory=list)
    #: Provider ids removed since the cursor.
    removed: list[str] = field(default_factory=list)
    #: Where the next page is, within this sync.
    next_page: str | None = None
    #: The cursor for the next sync, on the last page.
    cursor: str | None = None


def toolkit(provider: str) -> str:
    return (
        constants.PEOPLE_GOOGLE_TOOLKIT
        if provider == GOOGLE
        else constants.PEOPLE_MICROSOFT_TOOLKIT
    )


def fake_base() -> str | None:
    url = constants.PEOPLE_FAKE_PROVIDER_URL
    if not url:
        return None
    if str(constants.ENVIRONMENT).lower() not in FAKE_ENVIRONMENTS:
        return None
    return url.rstrip("/")


async def account_for(organization_id: int, user_id: int, provider: str) -> str | None:
    """The person's own connected account for this provider, or None.

    None also when per-person connections are off: then every connection is
    the workspace's, and a workspace's address book is not this person's.
    """
    if fake_base():
        return f"fake-{provider}-{user_id}"
    from api.services.integrations.composio import client, members

    if not client.is_configured() or members.member_scope(user_id) is None:
        return None
    wanted = toolkit(provider)
    for account in await client.connected_accounts(organization_id, user_id=user_id):
        if account.get("app") == wanted:
            return str(account["connected_account_id"])
    return None


async def _get(
    organization_id: int,
    user_id: int,
    account: str,
    url: str,
    params: dict[str, Any] | None,
) -> tuple[int, Any]:
    fake = fake_base()
    if fake:
        parts = urlsplit(url)
        prefix = "/google" if "googleapis" in parts.netloc else "/microsoft"
        target = f"{fake}{prefix}{parts.path}" + (
            f"?{parts.query}" if parts.query else ""
        )
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            response = await http.get(
                target, params=params, headers={"X-Fake-Account": account}
            )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None
    from api.constants import COMPOSIO_BASE_URL
    from api.services.integrations.composio import client

    endpoint = (
        url if not params else f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    )
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            response = await http.post(
                f"{COMPOSIO_BASE_URL}/api/v3/tools/execute/proxy",
                headers=client._headers(),
                json={
                    "endpoint": endpoint,
                    "method": "GET",
                    "connected_account_id": account,
                },
            )
    except httpx.HTTPError as exc:
        raise ProviderError("unreachable", "Could not reach the provider.") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("error", "The provider gave an unreadable answer.") from exc
    if response.status_code >= 400:
        raise ProviderError(
            "error", f"The connection refused the read ({response.status_code})."
        )
    status = body.get("status") if isinstance(body, dict) else None
    data = body.get("data") if isinstance(body, dict) else None
    return int(status or 200), data


def _check(status: int, body: Any) -> None:
    if status == 410:
        raise CursorExpired()
    if status in (401, 403):
        raise ProviderError(
            "no_permission",
            "The connection does not allow reading contacts. Connect it again "
            "and allow contacts.",
        )
    if status >= 400 or not isinstance(body, dict):
        raise ProviderError("error", f"The provider answered {status}.")


async def google_page(
    organization_id: int,
    user_id: int,
    account: str,
    *,
    cursor: str | None,
    page: str | None,
) -> Page:
    params: dict[str, Any] = {
        "personFields": GOOGLE_FIELDS,
        "pageSize": 500,
        "requestSyncToken": "true",
    }
    if cursor:
        params["syncToken"] = cursor
    if page:
        params["pageToken"] = page
    status, body = await _get(organization_id, user_id, account, GOOGLE_URL, params)
    if status == 400 and cursor and "sync" in str(body).lower():
        raise CursorExpired()
    _check(status, body)
    out = Page(next_page=body.get("nextPageToken"), cursor=body.get("nextSyncToken"))
    for item in body.get("connections") or []:
        if not isinstance(item, dict) or not item.get("resourceName"):
            continue
        if (item.get("metadata") or {}).get("deleted"):
            out.removed.append(item["resourceName"])
            continue
        names = item.get("names") or [{}]
        orgs = item.get("organizations") or [{}]
        out.contacts.append(
            Incoming(
                name=names[0].get("displayName")
                or " ".join(
                    p
                    for p in (names[0].get("givenName"), names[0].get("familyName"))
                    if p
                ),
                phones=[
                    p.get("canonicalForm") or p.get("value") or ""
                    for p in item.get("phoneNumbers") or []
                ],
                emails=[e.get("value") or "" for e in item.get("emailAddresses") or []],
                company=orgs[0].get("name"),
                relation=orgs[0].get("title"),
                external_id=item["resourceName"],
                etag=item.get("etag"),
            )
        )
    return out


async def microsoft_page(
    organization_id: int,
    user_id: int,
    account: str,
    *,
    cursor: str | None,
    page: str | None,
) -> Page:
    if page or cursor:
        status, body = await _get(
            organization_id, user_id, account, page or cursor, None
        )
    else:
        status, body = await _get(
            organization_id, user_id, account, GRAPH_URL, {"$select": GRAPH_SELECT}
        )
    _check(status, body)
    out = Page(
        next_page=body.get("@odata.nextLink"), cursor=body.get("@odata.deltaLink")
    )
    for item in body.get("value") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if "@removed" in item:
            out.removed.append(item["id"])
            continue
        phones = [item.get("mobilePhone") or ""]
        phones += list(item.get("businessPhones") or []) + list(
            item.get("homePhones") or []
        )
        out.contacts.append(
            Incoming(
                name=item.get("displayName")
                or " ".join(
                    p for p in (item.get("givenName"), item.get("surname")) if p
                ),
                phones=phones,
                emails=[
                    e.get("address") or "" for e in item.get("emailAddresses") or []
                ],
                company=item.get("companyName"),
                relation=item.get("jobTitle"),
                external_id=item["id"],
                etag=item.get("@odata.etag"),
            )
        )
    return out


async def read_page(
    provider: str,
    organization_id: int,
    user_id: int,
    account: str,
    *,
    cursor: str | None,
    page: str | None,
) -> Page:
    reader = google_page if provider == GOOGLE else microsoft_page
    return await reader(organization_id, user_id, account, cursor=cursor, page=page)
