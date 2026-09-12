"""Connecting an app and giving an agent access to it are two steps.

The chat could do the first and had no tool for the second. So a user said
"email the confirmation", authorized Gmail, was told it was connected -- and
their agent still could not send email. Nothing failed, nothing was logged,
and the discovery was a customer not getting their confirmation.

The tests below are mostly about refusals, because every input here arrives as
a string a model produced: the agent id, the app slug, the action slug. An
invented action is accepted by every layer below this one and fails on a live
call with somebody waiting.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder import tools as builder

ACTIONS = [
    {"slug": "GMAIL_SEND_EMAIL", "does": "Send an email"},
    {"slug": "GMAIL_FETCH_EMAILS", "does": "Read the inbox"},
]


def _agent(nodes=None):
    return SimpleNamespace(
        id=7,
        workflow_definition={
            "nodes": nodes
            if nodes is not None
            else [
                {"id": "n1", "type": "agentNode", "data": {"prompt": "Book it."}},
                {"id": "n2", "type": "startNode", "data": {}},
            ],
            "edges": [],
        },
    )


class TestTheStepThatWasMissing:
    def test_the_chat_is_given_a_way_to_attach_at_all(self):
        """The bug was an absent tool, so this is the regression: a catalogue
        that can connect an app and not attach one promises a capability it
        cannot deliver."""
        assert "attach_app_tool" in builder.TOOL_NAMES
        assert "list_app_actions" in builder.TOOL_NAMES

    def test_attaching_is_offered_after_connecting_so_the_model_reads_it_last(self):
        names = [t["name"] for t in builder.tool_schemas()]
        assert names.index("connect_app") < names.index("attach_app_tool")
        assert names.index("list_app_actions") < names.index("attach_app_tool")

    def test_the_instructions_say_connecting_is_not_enough(self):
        """The model has to know the two steps are separate. Left to infer it,
        it reports success after connect_app."""
        from api.services.agent_builder.session import SYSTEM_PROMPT

        assert "attach_app_tool" in SYSTEM_PROMPT


class TestAttaching:
    async def _attach(self, **overrides):
        args = {
            "session": AsyncMock(),
            "organization_id": 42,
            "user_id": 1,
            "workflow_id": 7,
            "app": "gmail",
            "action": "GMAIL_SEND_EMAIL",
            "name": "Email the confirmation",
            "description": "After the booking is confirmed.",
        }
        args.update(overrides)
        return await builder._attach_app_tool(**args)

    @pytest.mark.asyncio
    async def test_it_creates_the_tool_and_puts_it_on_the_agent(self):
        agent = _agent()
        created = SimpleNamespace(tool_uuid="tool-abc")
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["gmail"])
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=ACTIONS)
            ),
            patch.object(
                builder, "create_tool_for_user", AsyncMock(return_value=created)
            ),
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=agent)
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p1"
                )
            )
            db.update_workflow = AsyncMock()
            result = await self._attach()

        assert result["attached"] is True
        assert result["tool_uuid"] == "tool-abc"
        saved = db.update_workflow.await_args.kwargs["workflow_definition"]
        node = next(n for n in saved["nodes"] if n["id"] == "n1")
        assert node["data"]["tool_uuids"] == ["tool-abc"]

    @pytest.mark.asyncio
    async def test_it_never_publishes(self):
        """Same boundary revise_agent_facts keeps. A chat that can silently
        change what answers a clinic's phone is one bad turn from an outage."""
        agent = _agent()
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["gmail"])
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=ACTIONS)
            ),
            patch.object(
                builder,
                "create_tool_for_user",
                AsyncMock(return_value=SimpleNamespace(tool_uuid="t1")),
            ),
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=agent)
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p1"
                )
            )
            db.update_workflow = AsyncMock()
            result = await self._attach()

        assert result["published"] is False
        assert any("publish" in step.lower() for step in result["next_steps"])

    @pytest.mark.asyncio
    async def test_an_invented_action_is_refused_before_anything_is_written(self):
        """The failure this checks for happens mid-call otherwise. Every layer
        below here accepts the slug."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["gmail"])
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=ACTIONS)
            ),
            patch.object(builder, "create_tool_for_user", AsyncMock()) as create,
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach(action="GMAIL_SEND")

        assert "error" in result and "GMAIL_SEND" in result["error"]
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unconnected_app_is_refused_with_the_next_step(self):
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(builder, "connected_toolkits", AsyncMock(return_value=[])),
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach()

        assert "error" in result and "connect_app" in result["error"]

    @pytest.mark.asyncio
    async def test_another_tenants_agent_is_not_found(self):
        """The workflow id is a string a model produced. Scoped lookup, and
        `get_workflow_by_id` — the unscoped one — is never reached."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=None)
            result = await self._attach(workflow_id=9999)

        assert "error" in result and "9999" in result["error"]
        db.get_workflow.assert_awaited_once()
        assert db.get_workflow.await_args.kwargs["organization_id"] == 42

    @pytest.mark.asyncio
    async def test_a_catalogue_it_could_not_read_attaches_nothing(self):
        """None means "ask again", not "no such action". Attaching an
        unverified slug here would move the failure onto a live call."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["gmail"])
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=None)
            ),
            patch.object(builder, "create_tool_for_user", AsyncMock()) as create,
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach()

        assert "error" in result
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_workflow_id_that_is_not_a_number_is_refused(self):
        assert "error" in await self._attach(workflow_id="the dental one")

    @pytest.mark.asyncio
    async def test_an_agent_with_no_node_to_hold_it_says_so(self):
        """And says it before asking Composio anything. An agent that can never
        hold a tool should not cost two round trips to find that out."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(builder, "connected_toolkits", AsyncMock()) as connected,
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=_agent(nodes=[]))
            result = await self._attach()

        assert "error" in result and "/workflow/7" in result["error"]
        connected.assert_not_awaited()


class TestTheToolListIsAppendedNotReplaced:
    """An agent that already has a calendar and is given Gmail must end up
    with both. A tool list that silently drops what was there is the exact
    failure shape this codebase keeps finding."""

    def test_an_existing_tool_survives(self):
        definition = {
            "nodes": [
                {"id": "n1", "type": "agentNode", "data": {"tool_uuids": ["cal-1"]}}
            ]
        }
        assert builder._attach_to_nodes(definition, "gmail-1") == 1
        assert definition["nodes"][0]["data"]["tool_uuids"] == ["cal-1", "gmail-1"]

    def test_attaching_the_same_tool_twice_does_not_duplicate_it(self):
        definition = {
            "nodes": [
                {"id": "n1", "type": "agentNode", "data": {"tool_uuids": ["gmail-1"]}}
            ]
        }
        builder._attach_to_nodes(definition, "gmail-1")
        assert definition["nodes"][0]["data"]["tool_uuids"] == ["gmail-1"]

    def test_a_node_that_cannot_hold_tools_is_left_alone(self):
        definition = {"nodes": [{"id": "n1", "type": "startNode", "data": {}}]}
        assert builder._attach_to_nodes(definition, "t1") == 0
        assert "tool_uuids" not in definition["nodes"][0]["data"]


class TestListingActions:
    @pytest.mark.asyncio
    async def test_it_refuses_an_app_that_is_not_connected(self):
        """Describing a capability the user then agrees to, which fails one
        turn later, is worse than saying no now."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(builder, "connected_toolkits", AsyncMock(return_value=[])),
        ):
            result = await builder._list_app_actions(organization_id=42, app="gmail")
        assert "error" in result and "connect_app" in result["error"]

    @pytest.mark.asyncio
    async def test_an_app_with_nothing_attachable_is_not_an_error(self):
        """Empty and unreadable need different sentences, so they are
        different returns."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(builder, "connected_toolkits", AsyncMock(return_value=["x"])),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=[])
            ),
        ):
            result = await builder._list_app_actions(organization_id=42, app="x")
        assert result["actions"] == []
        assert "invent" in result["note"]

    @pytest.mark.asyncio
    async def test_a_failed_read_still_offers_the_skills_we_wrote(self):
        """A skill names its own action and is shape-checked at import, so a
        catalogue we cannot reach does not stop the jobs already written.
        Refusing everything would hide a working capability behind a vendor
        outage."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["gmail"])
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=None)
            ),
        ):
            result = await builder._list_app_actions(organization_id=42, app="gmail")

        assert "error" not in result
        assert result["skills"]
        assert result["actions"] == []
        assert "do not guess" in result["note"].lower()

    @pytest.mark.asyncio
    async def test_a_failed_read_on_an_app_we_have_not_written_for_refuses(self):
        """Nothing written and nothing readable is the one case where there is
        genuinely nothing to offer, and a guessed slug there fails on a live
        call."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["notion"])
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=None)
            ),
        ):
            result = await builder._list_app_actions(organization_id=42, app="notion")
        assert "error" in result and "guess" in result["error"]

    @pytest.mark.asyncio
    async def test_a_deployment_without_composio_says_so_rather_than_failing(self):
        with patch.object(builder, "composio_configured", lambda: False):
            assert "error" in await builder._list_app_actions(
                organization_id=42, app="gmail"
            )


class TestTheDispatcherRoutesThem:
    @pytest.mark.asyncio
    async def test_both_names_reach_their_handler(self):
        for name, target in (
            ("list_app_actions", "_list_app_actions"),
            ("attach_app_tool", "_attach_app_tool"),
        ):
            with patch.object(
                builder, target, AsyncMock(return_value={"ok": True})
            ) as handler:
                result = await builder.dispatch(
                    name,
                    {"app": "gmail", "workflow_id": 7, "action": "A", "name": "n"},
                    session=AsyncMock(),
                    organization_id=42,
                    user_id=1,
                )
            assert result == {"ok": True}, name
            handler.assert_awaited_once()


class TestOneToolPerPersonOrResource:
    """A clinic with three doctors has three calendars.

    `list_connected_apps` answers "is Google Calendar connected";
    `list_app_accounts` answers "whose", and that difference is the whole of
    per-doctor booking: one tool per calendar, named for that doctor, so the
    agent picks by name and an agent given one cannot reach the others.

    The alternative -- reading three calendars as one availability -- is the
    thing these tests exist to keep out. It would make a clinic look fully
    booked when one doctor is free, turning away a caller who would have seen
    anybody. Same bug as refusing an open slot, wearing a different hat.
    """

    ACCOUNTS = [
        {"connected_account_id": "ca_1", "app": "googlecalendar", "label": "Dr Ramesh"},
        {"connected_account_id": "ca_2", "app": "googlecalendar", "label": "Dr Priya"},
        {"connected_account_id": "ca_9", "app": "gmail", "label": "Front desk inbox"},
    ]

    @pytest.mark.asyncio
    async def test_it_lists_only_that_apps_accounts(self):
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder,
                "composio_connected_accounts",
                AsyncMock(return_value=self.ACCOUNTS),
            ),
        ):
            result = await builder._list_app_accounts(
                organization_id=42, app="googlecalendar"
            )
        assert [a["label"] for a in result["accounts"]] == ["Dr Ramesh", "Dr Priya"]

    @pytest.mark.asyncio
    async def test_it_tells_the_model_to_ask_rather_than_choose(self):
        """The labels are the operator's words. "Dr Ramesh" is obvious and
        "calendar-2" is not, and a tool pointed at the wrong doctor books the
        wrong doctor."""
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder,
                "composio_connected_accounts",
                AsyncMock(return_value=self.ACCOUNTS),
            ),
        ):
            result = await builder._list_app_accounts(
                organization_id=42, app="googlecalendar"
            )
        assert "ask the user which" in result["note"]

    @pytest.mark.asyncio
    async def test_nothing_connected_is_not_an_error(self):
        with (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "composio_connected_accounts", AsyncMock(return_value=[])
            ),
        ):
            result = await builder._list_app_accounts(organization_id=42, app="slack")
        assert result["accounts"] == []
        assert "connect_app" in result["note"]

    async def _attach(self, **overrides):
        args = {
            "session": AsyncMock(),
            "organization_id": 42,
            "user_id": 1,
            "workflow_id": 7,
            "app": "googlecalendar",
            "action": "GOOGLECALENDAR_CREATE_EVENT",
            "name": "Book with Dr Ramesh",
            "description": "",
            "connected_account_id": "ca_1",
        }
        args.update(overrides)
        return await builder._attach_app_tool(**args)

    def _patches(self, created=None):
        actions = [{"slug": "GOOGLECALENDAR_CREATE_EVENT", "does": "Create an event"}]
        return (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder,
                "connected_toolkits",
                AsyncMock(return_value=["googlecalendar"]),
            ),
            patch.object(
                builder, "composio_toolkit_actions", AsyncMock(return_value=actions)
            ),
            patch.object(
                builder,
                "composio_connected_accounts",
                AsyncMock(return_value=self.ACCOUNTS),
            ),
            patch.object(
                builder,
                "create_tool_for_user",
                AsyncMock(return_value=created or SimpleNamespace(tool_uuid="t1")),
            ),
        )

    @pytest.mark.asyncio
    async def test_the_account_reaches_the_tool_config(self):
        """Without this the tool acts on whichever calendar Composio picks,
        and "Book with Dr Ramesh" books somebody else."""
        p1, p2, p3, p4, p5 = self._patches()
        with p1, p2, p3, p4, p5 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p"
                )
            )
            db.update_workflow = AsyncMock()
            result = await self._attach()

        assert result["attached"] is True
        assert result["acts_on"] == "Dr Ramesh"
        config = create.await_args.args[0].definition.config
        assert config.connected_account_id == "ca_1"

    @pytest.mark.asyncio
    async def test_an_account_from_another_tenant_is_refused(self):
        """Checked against this organization's own accounts rather than
        trusted. Letting it through would point a tool at somebody else's
        calendar and fail on a live call instead of here."""
        p1, p2, p3, p4, p5 = self._patches()
        with p1, p2, p3, p4, p5 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach(connected_account_id="ca_someone_else")

        assert "error" in result and "ca_someone_else" in result["error"]
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_account_belonging_to_a_different_app_is_refused(self):
        """`ca_9` is real and is a Gmail account. Attaching it to a calendar
        action is a tool that cannot work, created without complaint."""
        p1, p2, p3, p4, p5 = self._patches()
        with p1, p2, p3, p4, p5 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach(connected_account_id="ca_9")

        assert "error" in result
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_omitting_it_is_allowed_for_a_business_with_only_one(self):
        """Most accounts have one of everything. Requiring the id would make
        every attach two round trips for no gain."""
        p1, p2, p3, p4, p5 = self._patches()
        with p1, p2, p3, p4, p5 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p"
                )
            )
            db.update_workflow = AsyncMock()
            result = await self._attach(connected_account_id="")

        assert result["attached"] is True
        assert create.await_args.args[0].definition.config.connected_account_id is None

    def test_the_instructions_refuse_to_merge_calendars(self):
        from api.services.agent_builder.session import SYSTEM_PROMPT

        assert "list_app_accounts" in SYSTEM_PROMPT
        assert "fully booked when one doctor is free" in SYSTEM_PROMPT


class TestSkillsCarryTheRuleWeAuthored:
    """A connector gives an agent access; it does not say when to act.

    That sentence is the difference between a working agent and one that
    emails a confirmation at the wrong moment -- and until now the chat handed
    a model a raw action slug and asked it to write the sentence freehand, per
    customer, every time. So the instruction governing a live phone call was
    improvised by a model rather than authored by anyone who knows the app.
    """

    def _tool(self):
        return SimpleNamespace(
            name="Book appointment", tool_uuid="u1", description="Books a slot."
        )

    async def _attach(self, **overrides):
        args = {
            "session": AsyncMock(),
            "organization_id": 42,
            "user_id": 1,
            "workflow_id": 7,
            "app": "gmail",
            "action": "",
            "name": "",
            "description": "",
            "skill": "email-the-confirmation",
        }
        args.update(overrides)
        return await builder._attach_app_tool(**args)

    def _patches(self):
        return (
            patch.object(builder, "composio_configured", lambda: True),
            patch.object(
                builder, "connected_toolkits", AsyncMock(return_value=["gmail"])
            ),
            patch.object(
                builder,
                "create_tool_for_user",
                AsyncMock(return_value=SimpleNamespace(tool_uuid="t1")),
            ),
        )

    @pytest.mark.asyncio
    async def test_a_skill_supplies_the_action_and_the_rule(self):
        p1, p2, p3 = self._patches()
        with p1, p2, p3 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p"
                )
            )
            db.update_workflow = AsyncMock()
            result = await self._attach()

        assert result["attached"] is True
        assert result["skill"] == "email-the-confirmation"
        request = create.await_args.args[0]
        assert request.definition.config.tool_slug == "GMAIL_SEND_EMAIL"
        # The authored rule is what a live agent reads.
        assert "read back to them" in request.description

    @pytest.mark.asyncio
    async def test_it_needs_no_catalogue_round_trip(self):
        """The action was authored and shape-checked at import. Requiring a
        vendor call would let an outage block a capability we already know is
        correct."""
        p1, p2, p3 = self._patches()
        with (
            p1,
            p2,
            p3,
            patch.object(builder, "composio_toolkit_actions", AsyncMock()) as actions,
            patch.object(builder, "db_client") as db,
        ):
            db.get_workflow = AsyncMock(return_value=_agent())
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p"
                )
            )
            db.update_workflow = AsyncMock()
            result = await self._attach()

        assert result["attached"] is True
        actions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_what_the_model_added_is_kept_alongside_the_rule(self):
        """The model may know something about *this* business that the skill
        cannot. Dropping it would make the skill worse than the freehand
        description it replaced."""
        p1, p2, p3 = self._patches()
        with p1, p2, p3 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            db.get_user_by_id = AsyncMock(
                return_value=SimpleNamespace(
                    id=1, selected_organization_id=42, provider_id="p"
                )
            )
            db.update_workflow = AsyncMock()
            await self._attach(description="Sunrise Dental signs off as 'Dr Anitha'.")

        described = create.await_args.args[0].description
        assert "read back to them" in described
        assert "Dr Anitha" in described

    @pytest.mark.asyncio
    async def test_an_invented_skill_is_refused(self):
        p1, p2, p3 = self._patches()
        with p1, p2, p3 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach(skill="just-do-the-thing")

        assert "error" in result and "just-do-the-thing" in result["error"]
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_skill_attached_to_the_wrong_app_is_refused(self):
        """ "Email the confirmation" on the calendar connector is a tool that
        cannot work, created without complaint."""
        p1, p2, p3 = self._patches()
        with p1, p2, p3 as create, patch.object(builder, "db_client") as db:
            db.get_workflow = AsyncMock(return_value=_agent())
            result = await self._attach(app="googlecalendar")

        assert "error" in result
        create.assert_not_awaited()

    def test_every_authored_skill_names_an_action_of_its_own_app(self):
        """Checked at import by the model validator, asserted here so the
        failure is a test rather than a startup crash in production."""
        from api.services.integrations.composio import skills

        assert skills.SKILLS
        for skill in skills.SKILLS:
            prefix = skill.app.replace("_", "").upper()
            assert skill.action.replace("_", "").startswith(prefix), skill.slug

    def test_no_two_skills_share_a_slug(self):
        """The slug is how the chat attaches one. Two with the same name means
        whichever was written last silently wins."""
        from api.services.integrations.composio import skills

        slugs = [s.slug for s in skills.SKILLS]
        assert len(slugs) == len(set(slugs))

    def test_the_risky_ones_say_when_not_to_act(self):
        """The expensive failures are all premature -- a payment link sent
        before the amount was agreed, a confirmation emailed to an address
        nobody read back. A rule with no negative case does not prevent them."""
        from api.services.integrations.composio import skills

        for slug in ("send-the-payment-link", "email-the-confirmation"):
            written = skills.get(slug)
            assert written is not None, slug
            assert any(
                phrase in written.use_when.lower()
                for phrase in ("never", "do not", "only after")
            ), slug
