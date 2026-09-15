"""Turn the Stratfiy/decibyl prompt pack into draft pack folders.

    python -m scripts.import_prompt_pack <prompts_dir> <shelf.ts> \\
        --ref <commit> [--out packs/drafts]

The prompt pack (``docs/product/prompts/*.md`` in Stratfiy/decibyl) is one
markdown file per shelf role: frontmatter with the role slug, then the
worker's instructions under fixed headings. The shelf (``data/shelf.ts``)
says which channels and tools the role has. This script joins the two into
``packs/drafts/<slug>/``, in the same shape as ``packs/live``, so a draft
loads through the same loader and runs through the same checks as a live
pack.

What it decides, and why, so the folders can be regenerated the same way:

* **Channels** come from the shelf. ``phone`` becomes inbound or outbound
  calling from the role's kind; a routine that phones people is an outbound
  call on a schedule. ``sms`` has no channel here and is dropped.
* **Stack** is the catalogue's own: the Indic voice stack for anything that
  speaks, the quiet stack for anything that does not. Same for guardrails.
* **The worker is one node** carrying the whole prompt, plus a close. The
  prompt pack is written as a single system prompt, and splitting it into a
  flow is a product decision for whoever promotes the draft.
* **``## Tests``** moves to ``references/tests.md``. They are Check-it
  scenarios, not instructions, and a prompt that carries its own test cases
  is a prompt that has been told what the judge will ask.
* **``{{variables}}``** become the pack's required facts, so the hire flow
  has a form to render. A handful are things the runtime knows (time of
  day, the date, the person on the line) and are left as template variables
  only.

Every draft is ``listed: false``. Nothing here reaches a shelf.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from api.services.agent_templates import (
    AgentTemplate,
    CallDirection,
    CallShape,
    TemplateEdge,
    TemplateNode,
)
from api.services.agent_templates._base import ScheduleShape
from api.services.agent_templates.catalogue import (
    _BASE_GUARDRAILS,
    _INDIC,
    _QUIET,
    _QUIET_GUARDRAILS,
    register_template,
)
from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    Publisher,
    RequiredConnector,
    RequiredFact,
)
from api.services.packs.folder import (
    DRAFTS_DIR,
    REFERENCES_DIR,
    SKILL_FILE,
    Source,
    render,
)

DECIBYL = Publisher(slug="decibyl", name="Decibyl", first_party=True)
LICENCE = (
    "Personas adapted from msitarzewski/agency-agents (MIT), rewritten for "
    "Decibyl in Stratfiy/decibyl"
)

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_VAR = re.compile(r"\{\{([a-z_]+)\}\}")
_PHONE_OPENING = re.compile(r'^- Phone: "(.+)"\s*$', re.MULTILINE)

#: Filled at run time, not by the business hiring the role.
RUNTIME_VARIABLES = {
    "time_of_day",
    "date",
    "today",
    "candidate_name",
    "client_name",
    "caller_name",
    "customer_name",
    "guest_name",
    "loan_reference",
    "period",
    "request_type",
    "staff_member",
}

#: Shelf tool slug -> the app slug our connectors use, with a label.
TOOLS = {
    "google-calendar": ("googlecalendar", "Google Calendar"),
    "google-sheets": ("googlesheets", "Google Sheets"),
    "google-drive": ("googledrive", "Google Drive"),
    "google-docs": ("googledocs", "Google Docs"),
    "whatsapp-business": ("whatsapp", "WhatsApp"),
    "email": ("gmail", "Email"),
    "hubspot": ("hubspot", "HubSpot"),
    "zoho-crm": ("zoho", "Zoho CRM"),
    "zoho-books": ("zoho_books", "Zoho Books"),
    "zoho-desk": ("zoho_desk", "Zoho Desk"),
    "freshdesk": ("freshdesk", "Freshdesk"),
    "tally": ("tally", "Tally"),
    "quickbooks": ("quickbooks", "QuickBooks"),
    "shopify": ("shopify", "Shopify"),
    "razorpay": ("razorpay", "Razorpay"),
    "rest-api": ("rest_api", "Your own API"),
}


def read_shelf(path: Path) -> dict[str, dict]:
    """The roles in ``data/shelf.ts``, by slug.

    The file is TypeScript, but the array literal is JSON: the site writes
    it with ``JSON.stringify``. Everything before ``= [`` is types and a
    docstring, and nothing after the array matters here.
    """
    text = path.read_text(encoding="utf-8")
    start = text.index("= [", text.index("export const shelf")) + 2
    depth = 0
    for end, char in enumerate(text[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                break
    roles = json.loads(text[start : end + 1])
    return {role["slug"]: role for role in roles}


def channels_for(role: dict) -> list[Channel]:
    kind = role["kind"]
    found: list[Channel] = []
    for channel in role["channels"]:
        if channel == "phone":
            if kind == "inbound":
                found.append(Channel.INBOUND_CALL)
            elif kind == "both":
                found.extend([Channel.INBOUND_CALL, Channel.OUTBOUND_CALL])
            else:
                found.append(Channel.OUTBOUND_CALL)
        elif channel == "whatsapp":
            found.append(Channel.WHATSAPP)
        elif channel == "email":
            found.append(Channel.EMAIL)
        elif channel == "web":
            found.append(Channel.WEB)
        # "sms" has no channel here and is dropped.
    if kind == "routine":
        found.append(Channel.SCHEDULED)
    return found


def direction_for(role: dict, channels: list[Channel]) -> CallDirection:
    if Channel.OUTBOUND_CALL in channels:
        return CallDirection.outbound
    if Channel.INBOUND_CALL in channels:
        return CallDirection.inbound
    if role["kind"] == "routine":
        return CallDirection.scheduled
    return CallDirection.message


def words(variable: str) -> str:
    return variable.replace("_", " ")


def fact_for(variable: str) -> RequiredFact:
    kind = FactKind.TEXT
    if variable.endswith(("_number", "_contact", "_phone", "_line")):
        kind = FactKind.PHONE
    elif variable.endswith("_email"):
        kind = FactKind.EMAIL
    elif variable.endswith("_hours"):
        kind = FactKind.HOURS
    elif variable in {"languages", "recipients", "deal_breakers", "report_metrics"}:
        kind = FactKind.LIST
    elif variable.endswith(("_days", "_minutes", "_threshold", "_retries")):
        kind = FactKind.NUMBER
    if variable.endswith(
        ("_tool", "_sheet", "_source", "_base", "_tracker", "_registry", "_table")
    ) or variable in {"calendar", "crm_tool", "books_tool"}:
        question = f"Which {words(variable)} should it use?"
    else:
        question = f"What is the {words(variable)}?"
    return RequiredFact(
        key=variable,
        question=question,
        kind=kind,
        used_for=f"Wherever the instructions say {{{{{variable}}}}}.",
    )


def split_prompt(text: str) -> tuple[dict[str, str], str, str]:
    """Frontmatter, the instructions, and the ``## Tests`` section."""
    match = _FRONT.match(text)
    if not match:
        raise SystemExit("prompt file has no frontmatter")
    front = {
        k.strip(): v.strip()
        for k, v in (line.split(":", 1) for line in match.group(1).splitlines())
    }
    body = text[match.end() :]
    # Drop the H1: the pack name is the title, and the body holds nodes.
    body = re.sub(r"^# .+\n", "", body, count=1).strip("\n")
    tests = ""
    if "\n## Tests" in body:
        body, tests = body.split("\n## Tests", 1)
        tests = "## Tests" + tests
        body = body.strip("\n")
    # Node sections are '## ', so the prompt's own headings step down one.
    body = re.sub(r"^## ", "### ", body, flags=re.MULTILINE)
    return front, body, tests


def build(slug: str, text: str, role: dict, ref: str, path: str):
    front, body, tests = split_prompt(text)
    if front.get("role") != slug:
        raise SystemExit(f"{path}: role {front.get('role')!r} is not {slug!r}")
    name = front.get("name") or role["name"]
    channels = channels_for(role)
    direction = direction_for(role, channels)
    speaks = direction in (CallDirection.inbound, CallDirection.outbound)

    variables = []
    for found in _VAR.findall(body):
        if found not in variables:
            variables.append(found)
    template_variables = {v: f"The {words(v)}." for v in variables}
    facts = [fact_for(v) for v in variables if v not in RUNTIME_VARIABLES]

    greeting = None
    if speaks:
        opening = _PHONE_OPENING.search(body)
        if opening:
            greeting = opening.group(1)

    template_id = "draft_" + slug.replace("-", "_")
    close_prompt = (
        "The job is done, or handed to a person. Confirm the next step in one "
        "sentence and close politely."
    )
    template = AgentTemplate(
        id=template_id,
        name=name,
        vertical=f"{role['sector']} — {role['industry']}",
        industry=role["sector"],
        function="",
        direction=direction,
        summary=role["does"][0] if role.get("does") else name,
        languages=["English", "Hindi"],
        stack=_INDIC if speaks else _QUIET,
        call_shape=CallShape(typical_call_seconds=120, typical_calls_per_month=1000)
        if speaks
        else None,
        schedule_shape=ScheduleShape(
            runs="every morning", typical_items_per_run=10, typical_runs_per_month=22
        )
        if direction == CallDirection.scheduled
        else None,
        nodes=[
            TemplateNode(type="startCall", name="Work", prompt=body, greeting=greeting),
            TemplateNode(type="endCall", name="Close", prompt=close_prompt),
        ],
        edges=[
            TemplateEdge(
                source="Work",
                target="Close",
                label="done",
                condition="The job is done or has been handed to a person",
            )
        ],
        guardrails=list(
            _QUIET_GUARDRAILS
            if direction == CallDirection.scheduled
            else _BASE_GUARDRAILS
        ),
        compliance_notes=[
            "Draft imported from the prompt pack. Not reviewed for a live shelf: "
            "read it against the role's tests before promoting it."
        ],
        example_requests=[role["posted"], name],
        template_variables=template_variables,
    )

    connectors = []
    seen = set()
    for tool in role.get("tools", []):
        app, label = TOOLS.get(tool, (tool.replace("-", "_"), tool))
        if app in seen:
            continue
        seen.add(app)
        connectors.append(
            RequiredConnector(
                app=app,
                label=label,
                used_for="Named on the shelf for this role.",
                required=False,
            )
        )

    # The pack validator resolves its template by id, so the draft's is
    # registered first -- the same thing the folder loader does on load.
    register_template(template)
    pack = AgentPack(
        slug=slug.replace("-", "_"),
        name=name,
        job=role["posted"],
        summary="; ".join(role["does"]) if role.get("does") else name,
        publisher=DECIBYL,
        channels=channels,
        template_id=template_id,
        industries=[role["industry"]],
        languages=["en", "hi"],
        required_facts=facts,
        required_connectors=connectors,
        listed=False,
    )
    source = Source(repo="Stratfiy/decibyl", ref=ref, path=path, licence=LICENCE)
    return pack, template, source, tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompts", type=Path, help="docs/product/prompts checkout")
    parser.add_argument("shelf", type=Path, help="data/shelf.ts checkout")
    parser.add_argument("--ref", required=True, help="commit the files came from")
    parser.add_argument("--out", type=Path, default=DRAFTS_DIR)
    args = parser.parse_args(argv)

    shelf = read_shelf(args.shelf)
    written = 0
    for file in sorted(args.prompts.glob("*.md")):
        if file.name == "README.md":
            continue
        slug = file.stem
        role = shelf.get(slug)
        if role is None:
            print(f"skipping {file.name}: not on the shelf", file=sys.stderr)
            continue
        rel = f"docs/product/prompts/{file.name}"
        pack, template, source, tests = build(
            slug, file.read_text(encoding="utf-8"), role, args.ref, rel
        )
        folder = args.out / pack.slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SKILL_FILE).write_text(
            render(pack, template, source=source, draft=True), encoding="utf-8"
        )
        if tests:
            (folder / REFERENCES_DIR).mkdir(exist_ok=True)
            (folder / REFERENCES_DIR / "tests.md").write_text(
                tests.rstrip("\n") + "\n", encoding="utf-8"
            )
        written += 1
        print(f"wrote {folder}")
    print(f"{written} drafts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
