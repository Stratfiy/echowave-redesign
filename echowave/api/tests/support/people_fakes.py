"""A fake Google People API and Microsoft Graph contacts, for People.

Answers the same requests ``services/people/providers.py`` makes, in the
providers' own shapes: pages, ``nextPageToken``/``nextSyncToken`` and
``metadata.deleted`` for Google; ``@odata.nextLink``/``@odata.deltaLink``
and ``@removed`` for Graph (links on graph.microsoft.com, as Graph gives
them). Each account (the ``X-Fake-Account`` header the provider code sends
in fake mode) has its own address book, a change counter, and faults:
``expire`` (the next sync token is refused, 410) and ``forbid`` (403, as a
connection without the contacts scope answers).

In tests it runs on a real port (``Running``); for a local stack:
``python -m api.tests.support.people_fakes --port 9200 --seed`` and
``PEOPLE_FAKE_PROVIDER_URL=http://127.0.0.1:9200`` in ``api/.env``.
"""

from __future__ import annotations

import argparse
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.tests.support.reach_fakes import free_port

PAGE_SIZE = 2
GRAPH = "https://graph.microsoft.com/v1.0/me/contacts/delta"

#: account -> {"version": int, "contacts": {id: contact}, "faults": set()}
BOOKS: dict[str, dict[str, Any]] = {}
#: Every request seen: (account, path, query) -- tests read it.
SEEN: list[tuple[str, str, dict[str, Any]]] = []


def reset() -> None:
    BOOKS.clear()
    SEEN.clear()


def book(account: str) -> dict[str, Any]:
    return BOOKS.setdefault(account, {"version": 0, "contacts": {}, "faults": set()})


def put(account: str, contact_id: str, **fields: Any) -> None:
    """Add or change a contact: ``name``, ``phones``, ``emails``, ``company``."""
    b = book(account)
    b["version"] += 1
    b["contacts"][contact_id] = {
        **fields,
        "id": contact_id,
        "v": b["version"],
        "deleted": False,
    }


def remove(account: str, contact_id: str) -> None:
    b = book(account)
    b["version"] += 1
    if contact_id in b["contacts"]:
        b["contacts"][contact_id].update(deleted=True, v=b["version"])


def _account(request: Request) -> str:
    return request.headers.get("x-fake-account", "")


def _changes(b: dict[str, Any], since: int | None) -> list[dict[str, Any]]:
    rows = sorted(b["contacts"].values(), key=lambda c: (c["v"], c["id"]))
    if since is None:
        return [c for c in rows if not c["deleted"]]
    return [c for c in rows if c["v"] > since]


async def google(request: Request) -> JSONResponse:
    account = _account(request)
    q = dict(request.query_params)
    SEEN.append((account, "google", q))
    b = book(account)
    if "forbid" in b["faults"]:
        return JSONResponse(
            {"error": {"code": 403, "status": "PERMISSION_DENIED"}}, 403
        )
    since = None
    if q.get("syncToken"):
        if "expire" in b["faults"]:
            b["faults"].discard("expire")
            return JSONResponse(
                {"error": {"code": 410, "status": "EXPIRED_SYNC_TOKEN"}}, 410
            )
        since = int(q["syncToken"].removeprefix("sync-"))
    rows = _changes(b, since)
    start = int(q.get("pageToken") or 0)
    page = rows[start : start + PAGE_SIZE]
    body: dict[str, Any] = {
        "connections": [
            {
                "resourceName": f"people/{c['id']}",
                "etag": f"e{c['v']}",
                "metadata": {"deleted": True} if c["deleted"] else {"sources": []},
                **(
                    {}
                    if c["deleted"]
                    else {
                        "names": [{"displayName": c.get("name")}],
                        "phoneNumbers": [{"value": p} for p in c.get("phones", [])],
                        "emailAddresses": [{"value": e} for e in c.get("emails", [])],
                        "organizations": (
                            [{"name": c["company"]}] if c.get("company") else []
                        ),
                    }
                ),
            }
            for c in page
        ],
        "totalPeople": len(rows),
    }
    if start + PAGE_SIZE < len(rows):
        body["nextPageToken"] = str(start + PAGE_SIZE)
    else:
        body["nextSyncToken"] = f"sync-{b['version']}"
    return JSONResponse(body)


async def microsoft(request: Request) -> JSONResponse:
    account = _account(request)
    q = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
    SEEN.append((account, "microsoft", q))
    b = book(account)
    if "forbid" in b["faults"]:
        return JSONResponse({"error": {"code": "ErrorAccessDenied"}}, 403)
    since = int(q["$deltatoken"]) if q.get("$deltatoken") else None
    if since is not None and "expire" in b["faults"]:
        b["faults"].discard("expire")
        return JSONResponse({"error": {"code": "SyncStateNotFound"}}, 410)
    rows = _changes(b, since)
    start = int(q.get("$skiptoken") or 0)
    page = rows[start : start + PAGE_SIZE]
    value = []
    for c in page:
        if c["deleted"]:
            value.append({"id": c["id"], "@removed": {"reason": "deleted"}})
            continue
        phones = list(c.get("phones", []))
        value.append(
            {
                "id": c["id"],
                "@odata.etag": f"W/e{c['v']}",
                "displayName": c.get("name"),
                "mobilePhone": phones[0] if phones else None,
                "businessPhones": phones[1:],
                "homePhones": [],
                "emailAddresses": [
                    {"address": e, "name": c.get("name")} for e in c.get("emails", [])
                ],
                "companyName": c.get("company"),
            }
        )
    body: dict[str, Any] = {"value": value}
    keep = {"$deltatoken": q["$deltatoken"]} if q.get("$deltatoken") else {}
    if start + PAGE_SIZE < len(rows):
        body["@odata.nextLink"] = (
            f"{GRAPH}?{urlencode({**keep, '$skiptoken': start + PAGE_SIZE})}"
        )
    else:
        body["@odata.deltaLink"] = f"{GRAPH}?{urlencode({'$deltatoken': b['version']})}"
    return JSONResponse(body)


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/google/v1/people/me/connections", google),
            Route("/microsoft/v1.0/me/contacts/delta", microsoft),
        ]
    )


class Running:
    """The fake on a background thread, for a test session."""

    def __init__(self, port: int | None = None) -> None:
        import uvicorn

        self.port = port or free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                build_app(), host="127.0.0.1", port=self.port, log_level="warning"
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "Running":
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def seed(user_ids: list[int]) -> None:
    """A few contacts for a local run, in every listed person's books."""
    for uid in user_ids:
        g = f"fake-google-{uid}"
        put(
            g,
            "c1",
            name="Ravi Kumar",
            phones=["+91 98765 43210"],
            emails=["ravi@example.in"],
            company="Kumar Traders",
        )
        put(
            g,
            "c2",
            name="Priya Sharma",
            phones=["09812345678"],
            emails=["priya@example.in"],
        )
        put(
            g,
            "c3",
            name="Anil Mehta",
            phones=["+91-99887-76655"],
            company="Mehta & Sons",
        )
        m = f"fake-microsoft-{uid}"
        put(m, "m1", name="Ravi K", phones=["9876543210"], company="Kumar Traders")
        put(m, "m2", name="Sunita Rao", emails=["sunita@example.org"])


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--seed", type=int, nargs="*", default=[])
    args = parser.parse_args()
    seed(args.seed)
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port)
