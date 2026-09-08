"""Ready-made tools for the apps businesses already run on.

An HTTP tool has been configurable for a long time and the editor renders it
generically. What it does not do is help: the operator gets an empty method, an
empty URL and an empty parameter list, and has to go and read Zoho's API
reference to fill them in. The gap between "we support Zoho" and "somebody
actually got Zoho working on a call" is that reading.

So this is a catalogue of the handful of things an agent does *on a phone call*
against each app, with the URL, the method and the parameters already written.
Picking one seeds an ordinary `http_api` tool; editing it afterwards edits that
copy. Nothing here is referenced by id at runtime — an operator who tuned a URL
for their own account must not have it replaced the next time this file is
edited. Same rule as `workflow/extraction_library.py`, and for the same reason.

**Curated, not generated.** Zoho's REST API has hundreds of endpoints and it is
tempting to generate a tool per endpoint from their spec. Do not: a model handed
two hundred tools picks worse, not better, and most of those endpoints are
meaningless mid-conversation. Vapi ships four hand-written tools for GoHighLevel
rather than its whole API surface, which is the right instinct. Four or five
verbs per app, chosen for what a caller actually asks for.

**These seed an `http_api` tool, deliberately.** Not a new `ToolCategory`. A
category means an enum value, a migration, a runtime dispatch branch and a
handler to test, per app, forever. A template is data: adding HubSpot is an
entry in this file, and it runs down the HTTP path that is already the most
exercised one in the codebase.

**No secret is in here.** Every entry expects an `oauth2` credential the
operator connected themselves, referenced by `credential_uuid` — see
`services/integrations/oauth2.py`. The templates carry the shape; the account
is theirs.

Adding an app is adding entries to `_LIBRARY`. Vendors are free-form strings,
ordered by first appearance, so a new app needs no registration anywhere.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

CATALOG_INTEGRATION_TOOLS = "integration_tools"


class LibraryToolParameter(BaseModel):
    """One parameter the agent fills in, seeding a `ToolParameter`."""

    name: str
    type: str = "string"
    description: str
    required: bool = True


class LibraryTool(BaseModel):
    """One catalogue entry, and the `http_api` tool it seeds."""

    key: str = Field(description="Stable id for this entry. Not stored on the tool.")
    vendor: str = Field(description="Section heading in the picker.")
    display_name: str = Field(description="What the picker lists it as.")
    summary: str = Field(description="One line on what it does during a call.")

    #: Seeds the tool's name and description. The description is what the model
    #: reads to decide whether to call it, so it is written as an instruction
    #: about *when*, not a restatement of the name.
    tool_name: str
    tool_description: str

    method: str = "GET"
    url: str
    parameters: list[LibraryToolParameter] = Field(default_factory=list)

    #: What the operator still has to do. Rendered next to the entry, because
    #: a template that silently needs a region change is worse than no
    #: template — it fails on a live call rather than at setup.
    setup_note: str = ""


class ToolLibraryResponse(BaseModel):
    catalog: str
    vendors: list[str]
    tools: list[LibraryTool]


_ZOHO = "Zoho CRM"
_HUBSPOT = "HubSpot"

#: Repeated on every Zoho entry rather than said once, because the datacentre
#: is the single most common way a Zoho integration fails and it fails with
#: `invalid_client`, which reads as a bad secret.
_ZOHO_DC = (
    "Change .in to your account's datacentre (.com, .eu, .com.au) — the wrong "
    "one returns invalid_client and looks like a bad credential."
)


_LIBRARY: tuple[LibraryTool, ...] = (
    # ------------------------------------------------------------------ Zoho
    LibraryTool(
        key="zoho_find_contact_by_phone",
        vendor=_ZOHO,
        display_name="Find the caller",
        summary="Look the caller up by their number before saying anything else.",
        tool_name="find_contact",
        tool_description=(
            "Look up the person calling, by phone number, in the CRM. Call this "
            "at the start of a call when you need to know whether this is an "
            "existing customer, what their name is, or what they last bought. "
            "If nothing comes back, treat them as a new caller."
        ),
        method="GET",
        url="https://www.zohoapis.in/crm/v8/Contacts/search",
        parameters=[
            LibraryToolParameter(
                name="phone",
                description=(
                    "The caller's phone number in the format the CRM stores, "
                    "usually with the country code."
                ),
            )
        ],
        setup_note=_ZOHO_DC,
    ),
    LibraryTool(
        key="zoho_create_lead",
        vendor=_ZOHO,
        display_name="Create a lead",
        summary="Write a new enquiry into the CRM while the caller is still on.",
        tool_name="create_lead",
        tool_description=(
            "Create a new lead in the CRM. Call this once you have the caller's "
            "name and what they are enquiring about, and only for someone who "
            "is not already a contact."
        ),
        method="POST",
        url="https://www.zohoapis.in/crm/v8/Leads",
        parameters=[
            LibraryToolParameter(
                name="last_name",
                description="The caller's name. Zoho requires it on a lead.",
            ),
            LibraryToolParameter(
                name="phone",
                description="The caller's phone number.",
            ),
            LibraryToolParameter(
                name="company",
                description="Their company, if they gave one.",
                required=False,
            ),
            LibraryToolParameter(
                name="description",
                description="What they are enquiring about, in one or two lines.",
                required=False,
            ),
        ],
        setup_note=_ZOHO_DC,
    ),
    LibraryTool(
        key="zoho_add_note",
        vendor=_ZOHO,
        display_name="Log a note on the record",
        summary="Leave what was agreed on the contact, during the call.",
        tool_name="add_note",
        tool_description=(
            "Attach a note to a CRM record. Call this when something was agreed "
            "or promised that the next person handling this account needs to "
            "know. Requires the record id from an earlier lookup."
        ),
        method="POST",
        url="https://www.zohoapis.in/crm/v8/Notes",
        parameters=[
            LibraryToolParameter(
                name="Parent_Id",
                description="The id of the contact or lead, from the earlier lookup.",
            ),
            LibraryToolParameter(
                name="Note_Title",
                description="A short title, such as 'Callback requested'.",
            ),
            LibraryToolParameter(
                name="Note_Content",
                description="What was said or agreed, in plain sentences.",
            ),
        ],
        setup_note=_ZOHO_DC,
    ),
    LibraryTool(
        key="zoho_find_deal",
        vendor=_ZOHO,
        display_name="Check an order or deal",
        summary="Answer 'where is my order' without transferring the caller.",
        tool_name="find_deal",
        tool_description=(
            "Look up a deal or order by its name or number. Call this when the "
            "caller asks about the status of something they have already "
            "bought or enquired about."
        ),
        method="GET",
        url="https://www.zohoapis.in/crm/v8/Deals/search",
        parameters=[
            LibraryToolParameter(
                name="word",
                description="The order number or deal name the caller gave.",
            )
        ],
        setup_note=_ZOHO_DC,
    ),
    # --------------------------------------------------------------- HubSpot
    LibraryTool(
        key="hubspot_find_contact_by_phone",
        vendor=_HUBSPOT,
        display_name="Find the caller",
        summary="Look the caller up by number before saying anything else.",
        tool_name="find_contact",
        tool_description=(
            "Look up the person calling, by phone number. Call this at the "
            "start of a call to find out whether they are an existing contact "
            "and what is known about them."
        ),
        method="POST",
        url="https://api.hubapi.com/crm/v3/objects/contacts/search",
        parameters=[
            LibraryToolParameter(
                name="phone",
                description="The caller's phone number.",
            )
        ],
        setup_note=(
            "HubSpot's search takes a filter body; the parameter is mapped into "
            "it. Give the private app the crm.objects.contacts.read scope."
        ),
    ),
    LibraryTool(
        key="hubspot_create_contact",
        vendor=_HUBSPOT,
        display_name="Create a contact",
        summary="Write a new caller into HubSpot during the call.",
        tool_name="create_contact",
        tool_description=(
            "Create a new contact. Call this for a caller who is not already in "
            "the CRM, once you have their name and number."
        ),
        method="POST",
        url="https://api.hubapi.com/crm/v3/objects/contacts",
        parameters=[
            LibraryToolParameter(name="firstname", description="Their first name."),
            LibraryToolParameter(
                name="lastname", description="Their surname.", required=False
            ),
            LibraryToolParameter(name="phone", description="Their phone number."),
            LibraryToolParameter(
                name="email", description="Their email, if given.", required=False
            ),
        ],
        setup_note="Needs the crm.objects.contacts.write scope.",
    ),
    LibraryTool(
        key="hubspot_log_call",
        vendor=_HUBSPOT,
        display_name="Log the call on the contact",
        summary="Put the call and its outcome on the timeline.",
        tool_name="log_call",
        tool_description=(
            "Record this call against a contact's timeline, with a note of what "
            "was discussed. Call this near the end of a conversation that "
            "should be visible to whoever picks the account up next."
        ),
        method="POST",
        url="https://api.hubapi.com/crm/v3/objects/calls",
        parameters=[
            LibraryToolParameter(
                name="hs_call_body",
                description="What was discussed and agreed, in plain sentences.",
            ),
            LibraryToolParameter(
                name="hs_call_title",
                description="A short title for the call.",
                required=False,
            ),
        ],
        setup_note="Needs the crm.objects.calls.write scope.",
    ),
)


def vendors() -> list[str]:
    """Section headings, in first-appearance order."""
    seen: list[str] = []
    for entry in _LIBRARY:
        if entry.vendor not in seen:
            seen.append(entry.vendor)
    return seen


def all_tools() -> list[LibraryTool]:
    return list(_LIBRARY)


def library() -> ToolLibraryResponse:
    return ToolLibraryResponse(
        catalog=CATALOG_INTEGRATION_TOOLS,
        vendors=vendors(),
        tools=all_tools(),
    )


def find(key: str) -> LibraryTool | None:
    return next((entry for entry in _LIBRARY if entry.key == key), None)


def seed_definition(entry: LibraryTool, *, credential_uuid: str | None = None) -> dict:
    """The `http_api` tool definition this entry seeds.

    Returned as a plain dict rather than an `HttpApiToolDefinition` so the
    catalogue can be served and seeded without importing the tool schemas —
    and so an entry written before a new config field still produces a valid
    definition rather than failing validation on something it never knew about.
    """
    return {
        "schema_version": 1,
        "type": "http_api",
        "config": {
            "method": entry.method,
            "url": entry.url,
            "credential_uuid": credential_uuid,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                }
                for p in entry.parameters
            ],
        },
    }
