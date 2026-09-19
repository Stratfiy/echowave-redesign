"""The trust page's facts, readable without an account.

A security review happens before somebody signs up. The two things that must
hold: the route takes no authentication, and the sub-processor list is the
derived one rather than a copy typed out beside it -- a hand-maintained list
is a written claim that goes false the first time a vendor is added.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import public_trust
from api.services.privacy.subprocessors import Subprocessor

app = FastAPI()
app.include_router(public_trust.router, prefix="/api/v1")
client = TestClient(app, raise_server_exceptions=False)

_LISTED = [
    Subprocessor(
        name="deepgram",
        purpose="Speech recognition",
        data="Call audio",
        basis="configured",
    ),
    Subprocessor(
        name="Amazon Web Services",
        purpose="Application hosting, database and object storage",
        data="All customer and call data at rest",
        basis="infrastructure",
    ),
]


@pytest.fixture(autouse=True)
def _derived(monkeypatch):
    @asynccontextmanager
    async def _session():
        yield SimpleNamespace()

    async def _in_use(_session):
        return _LISTED

    monkeypatch.setattr(
        public_trust.db_client, "async_session", _session, raising=False
    )
    monkeypatch.setattr(public_trust.subprocessors, "in_use", _in_use)


def test_it_answers_without_a_login():
    response = client.get("/api/v1/public/trust")

    assert response.status_code == 200


def test_it_lists_the_derived_subprocessors_with_their_basis():
    body = client.get("/api/v1/public/trust").json()

    assert [s["name"] for s in body["subprocessors"]] == [
        "deepgram",
        "Amazon Web Services",
    ]
    # The basis is the honest part: a reader can tell a vendor we hold a key
    # for from the hosting nobody chose per deployment.
    assert {s["basis"] for s in body["subprocessors"]} == {
        "configured",
        "infrastructure",
    }


def test_it_says_how_long_things_are_kept_and_where_they_live():
    body = client.get("/api/v1/public/trust").json()

    assert body["retention"]["recording_days"] > 0
    assert body["retention"]["transcript_days"] > 0
    # The window the list above was built from, so "in use" is not a guess.
    assert body["retention"]["subprocessor_window_days"] > 0
    assert body["region"]


def test_it_names_somebody_to_complain_to():
    """DPDP s13: a Data Principal has to have a person to write to."""
    body = client.get("/api/v1/public/trust").json()

    assert body["grievance_officer"]["email"]


def test_it_says_nothing_about_any_one_account():
    """The per-account answer stays behind a login. Answering "did this
    account use Deepgram" for a stranger would itself be a disclosure."""
    body = client.get("/api/v1/public/trust").json()

    assert "organization" not in body
    assert "organizations" not in body
