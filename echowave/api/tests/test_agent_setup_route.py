"""GET /workflow/{id}/setup — the questions a seller answers.

The derivation itself is covered in test_setup_fields.py. This covers the
route's own two decisions: which definition it reads, and that it stays scoped
to the caller's organization.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes import workflow as workflow_routes


def definition(prompt: str) -> dict:
    return {"nodes": [{"id": "a", "type": "agentNode", "data": {"prompt": prompt}}]}


def version(prompt: str, values: dict | None = None):
    return SimpleNamespace(
        workflow_json=definition(prompt),
        template_context_variables=values or {},
    )


@pytest.fixture
def db(monkeypatch):
    d = MagicMock()
    d.get_workflow = AsyncMock(
        return_value=SimpleNamespace(
            id=1,
            name="Sharma Dental",
            released_definition=version("Call {{business_name}}"),
        )
    )
    d.get_draft_version = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_routes, "db_client", d)
    return d


user = SimpleNamespace(id=9, selected_organization_id=7)


class TestReading:
    @pytest.mark.asyncio
    async def test_it_returns_the_fields_and_what_is_missing(self, db):
        res = await workflow_routes.get_agent_setup(1, user=user)
        assert [f.name for f in res.fields] == ["business_name"]
        assert res.missing == ["business_name"]
        assert res.workflow_name == "Sharma Dental"

    @pytest.mark.asyncio
    async def test_an_answered_field_comes_back_with_its_value(self, db):
        db.get_workflow.return_value = SimpleNamespace(
            id=1,
            name="Sharma Dental",
            released_definition=version(
                "Call {{business_name}}", {"business_name": "Sharma Dental"}
            ),
        )
        res = await workflow_routes.get_agent_setup(1, user=user)
        assert res.fields[0].value == "Sharma Dental"
        assert res.missing == []

    @pytest.mark.asyncio
    async def test_the_draft_wins_over_the_published_version(self, db):
        """The editor acts on the draft, so this form must too. Reading the
        published version would show a seller the questions for the agent that
        is live, not the one they are setting up."""
        db.get_draft_version.return_value = version("Ask about {{opening_hours}}")
        res = await workflow_routes.get_agent_setup(1, user=user)
        assert [f.name for f in res.fields] == ["opening_hours"]

    @pytest.mark.asyncio
    async def test_a_value_of_none_does_not_become_the_string_none(self, db):
        """A JSON null in the stored variables reaching the form as "None" is
        the sort of thing that gets saved back and then said out loud."""
        db.get_draft_version.return_value = version(
            "Call {{business_name}}", {"business_name": None}
        )
        res = await workflow_routes.get_agent_setup(1, user=user)
        assert res.fields[0].value == ""


class TestScoping:
    @pytest.mark.asyncio
    async def test_it_looks_the_workflow_up_by_organization(self, db):
        """An id from the URL proves nothing about who owns the agent."""
        await workflow_routes.get_agent_setup(1, user=user)
        assert db.get_workflow.await_args.kwargs["organization_id"] == 7

    @pytest.mark.asyncio
    async def test_another_organizations_workflow_is_a_404(self, db):
        db.get_workflow.return_value = None
        with pytest.raises(HTTPException) as exc:
            await workflow_routes.get_agent_setup(1, user=user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_workflow_with_no_definition_is_a_404_not_a_crash(self, db):
        db.get_workflow.return_value = SimpleNamespace(
            id=1, name="Empty", released_definition=None
        )
        with pytest.raises(HTTPException) as exc:
            await workflow_routes.get_agent_setup(1, user=user)
        assert exc.value.status_code == 404
