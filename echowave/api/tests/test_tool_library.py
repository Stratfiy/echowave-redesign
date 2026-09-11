"""The catalogue that turns "we support Zoho" into somebody having it working.

An HTTP tool has always been configurable; what was missing was help. These
tests hold the two things that make a template better than an empty form rather
than worse:

* **it seeds a definition the server will actually accept** — a template that
  produces something `CreateToolRequest` rejects is a form that fails on save
  and teaches the operator nothing, and
* **it says what is still theirs to do** — a Zoho URL carrying the wrong
  datacentre fails on a live call with `invalid_client`, which reads as a bad
  credential and sends people to the wrong problem entirely.
"""

from __future__ import annotations

import pytest

from api.schemas.tool import HttpApiToolDefinition
from api.services.integrations import tool_library


class TestTheCatalogue:
    def test_it_is_grouped_by_vendor_in_first_appearance_order(self):
        assert tool_library.vendors() == ["Zoho CRM", "HubSpot", "Shopify"]

    def test_every_key_is_unique(self):
        """Keys address an entry from the UI; two the same silently shadow."""
        keys = [t.key for t in tool_library.all_tools()]
        assert len(keys) == len(set(keys))

    def test_it_is_curated_not_generated(self):
        """Zoho has hundreds of endpoints and a model handed two hundred tools
        picks worse, not better. If this ever fails because somebody generated
        from a vendor spec, that is the thing to reconsider — not the number."""
        for vendor in tool_library.vendors():
            count = sum(1 for t in tool_library.all_tools() if t.vendor == vendor)
            assert 1 <= count <= 8, f"{vendor} has {count} tools"

    def test_lookup_by_key(self):
        assert tool_library.find("zoho_find_contact_by_phone") is not None
        assert tool_library.find("nope") is None


class TestWhatAnEntrySeeds:
    @pytest.mark.parametrize("entry", tool_library.all_tools(), ids=lambda e: e.key)
    def test_the_definition_validates_as_an_http_tool(self, entry):
        """The whole point of a template is that it saves. One that produces a
        definition the schema rejects is worse than an empty form, because the
        operator now believes the product is broken rather than unfinished."""
        definition = tool_library.seed_definition(entry, credential_uuid="cred-1")
        parsed = HttpApiToolDefinition.model_validate(definition)
        assert parsed.type == "http_api"
        assert parsed.config.credential_uuid == "cred-1"

    @pytest.mark.parametrize("entry", tool_library.all_tools(), ids=lambda e: e.key)
    def test_it_carries_no_secret_and_no_hardcoded_account(self, entry):
        """Authentication is the operator's connected credential, never
        something baked into a template shipped to every customer."""
        definition = tool_library.seed_definition(entry)
        config = definition["config"]
        assert config.get("headers") in (None, {})
        blob = repr(definition).lower()
        for smell in ("bearer ", "api_key=", "token=", "secret"):
            assert smell not in blob

    @pytest.mark.parametrize("entry", tool_library.all_tools(), ids=lambda e: e.key)
    def test_it_previews_without_a_credential(self, entry):
        """The browse view shows the shape before an account is connected."""
        definition = tool_library.seed_definition(entry)
        assert definition["config"]["credential_uuid"] is None

    @pytest.mark.parametrize("entry", tool_library.all_tools(), ids=lambda e: e.key)
    def test_every_url_is_https(self, entry):
        assert entry.url.startswith("https://")


class TestWhatTheModelReads:
    @pytest.mark.parametrize("entry", tool_library.all_tools(), ids=lambda e: e.key)
    def test_the_description_says_when_to_call_it(self, entry):
        """A description that restates the name tells the model nothing about
        timing, and mis-timed tool calls are the common failure. Every entry is
        written as an instruction, so every one should say 'call this'."""
        assert "call this" in entry.tool_description.lower()

    @pytest.mark.parametrize("entry", tool_library.all_tools(), ids=lambda e: e.key)
    def test_every_parameter_tells_the_model_what_to_put_in_it(self, entry):
        for parameter in entry.parameters:
            assert parameter.description.strip(), f"{entry.key}.{parameter.name}"


class TestWhatIsStillTheOperatorsJob:
    def test_every_zoho_entry_warns_about_the_datacentre(self):
        """The single most common way a Zoho integration fails, and it fails
        with `invalid_client` — which reads as a bad secret and sends people to
        re-check a credential that was fine."""
        zoho = [t for t in tool_library.all_tools() if t.vendor == "Zoho CRM"]
        assert zoho
        for entry in zoho:
            assert "datacentre" in entry.setup_note.lower()

    def test_every_hubspot_entry_names_the_scope_it_needs(self):
        """A missing scope is a 403 on a live call rather than at setup."""
        hubspot = [t for t in tool_library.all_tools() if t.vendor == "HubSpot"]
        assert hubspot
        for entry in hubspot:
            assert "scope" in entry.setup_note.lower()
