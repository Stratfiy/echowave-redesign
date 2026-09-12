import pytest

from api.schemas.tool import TransferCallConfig


def test_transfer_call_destination_accepts_initial_context_template():
    config = TransferCallConfig(
        destination="{{initial_context.transfer_destination}}",
    )

    assert config.destination == "{{initial_context.transfer_destination}}"


def test_transfer_call_destination_accepts_provider_specific_literal():
    config = TransferCallConfig(destination="provider-specific-destination")

    assert config.destination == "provider-specific-destination"


def test_transfer_call_static_allows_empty_draft_destination():
    config = TransferCallConfig(destination_source="static", destination="")

    assert config.destination_source == "static"
    assert config.destination == ""


def test_transfer_call_dynamic_requires_resolver():
    with pytest.raises(ValueError, match="resolver is required"):
        TransferCallConfig(destination_source="dynamic", destination="")


def test_transfer_call_dynamic_accepts_resolver_without_destination():
    config = TransferCallConfig(
        destination_source="dynamic",
        destination="",
        resolver={
            "type": "http",
            "url": "https://crm.example.com/resolve-transfer",
        },
    )

    assert config.destination_source == "dynamic"
    assert config.destination == ""
    assert config.resolver is not None


class TestEveryToolTypeIsCreatable:
    """``category`` is derived from ``definition.type`` and then validated
    against ``ToolCategoryValue``, a hand-maintained Literal. A type present in
    ``ToolDefinition`` and absent from that Literal is a tool nobody can
    create -- through the REST route, the service layer or MCP alike, all three
    of which validate the same request.

    ``composio`` was missing. The integration connected, the connector screen
    listed apps, and the last step -- attaching one to an agent -- returned 422
    on a field the caller never sent. The field validator directly below the
    Literal checks against the whole ``ToolCategory`` enum, so the intent was
    always every value; the second list simply drifted.

    This walks the union rather than restating it, so the next type added is
    covered by writing it once.
    """

    def _types(self) -> set[str]:
        from typing import get_args, get_type_hints

        from api.schemas.tool import ToolDefinition

        # ToolDefinition is Annotated[Union[...], Field(discriminator=...)].
        members = get_args(get_args(ToolDefinition)[0])
        return {
            get_args(get_type_hints(member)["type"])[0]
            for member in members
            if member is not type(None)
        }

    def test_the_literal_covers_every_definition_type(self):
        from typing import get_args

        from api.schemas.tool import ToolCategoryValue

        missing = self._types() - set(get_args(ToolCategoryValue))
        assert not missing, (
            f"{sorted(missing)} can be expressed as a definition and refused as "
            f"a category, so no caller can create one"
        )

    def test_composio_specifically_because_it_shipped_broken(self):
        from api.schemas.tool import CreateToolRequest

        request = CreateToolRequest(
            name="Send the confirmation",
            definition={
                "type": "composio",
                "config": {"toolkit": "gmail", "tool_slug": "GMAIL_SEND_EMAIL"},
            },
        )
        assert request.category == "composio"

    def test_the_enum_and_the_literal_do_not_disagree(self):
        """Two lists of the same thing is the defect; while both exist they
        have to agree, or the error a caller gets depends on which one it
        reaches first."""
        from typing import get_args

        from api.enums import ToolCategory
        from api.schemas.tool import ToolCategoryValue

        assert set(get_args(ToolCategoryValue)) <= {c.value for c in ToolCategory}
