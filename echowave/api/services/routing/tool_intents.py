"""Which of Decibyl's tools a message points at.

Behind ``lean_tools``. A quick turn is offered a core set of tools, not all
of them (``services/workflow/lean_tools.py``); this is how a message asks for
one that is not in it. Two ways, and either is enough:

* **By name.** The person (or a template) says the tool: ``check_bot``,
  "check bot", "run script". Matched on the tool's own name with the
  underscores read as spaces, so no table can be out of date for a tool that
  is named.
* **By intent.** A plain-words table below: "make a poster" points at the
  image tool, "rank these suppliers" at the table tools.

**Wrong in the safe direction.** A pattern that matches too much sends a few
hundred tokens of schema nobody needed. A pattern that matches too little
costs nothing worse than one round-trip, because the model can always call
``more_tools`` (and is told which tools it was not given). So these patterns
are written loose, and a tool that is not in this table is simply never
flagged by intent: it is still found by name, still found by ``more_tools``,
and ``test_lean_tools`` fails if a tool is added that is in neither the core
set nor this table, so the omission is seen on the pull request that makes it.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Intent -> (pattern, the tools it points at). Names only; whether a tool is
#: switched on for this account is decided where the full list is built, and a
#: name here that is not offered is ignored.
INTENTS: dict[str, tuple[re.Pattern[str], tuple[str, ...]]] = {
    "images": (
        re.compile(
            r"\b(images?|pictures?|photos?|posters?|logos?|banners?|creatives?|"
            r"graphics?|artwork|illustrations?|flyers?|thumbnails?)\b",
            re.I,
        ),
        ("make_images",),
    ),
    "leads": (
        re.compile(
            r"\b(leads?|prospects?|outreach|cold (email|mail)s?|apollo|"
            r"lead[- ]gen)\b",
            re.I,
        ),
        ("find_leads", "draft_outreach", "save_prospects", "lookup_person"),
    ),
    "people": (
        re.compile(r"\b(who is|contact details?|phone number of|email of)\b", re.I),
        ("lookup_person",),
    ),
    "documents": (
        re.compile(
            r"\b(contracts?|letters?|quotations?|quotes?|proposals?|agreements?|"
            r"purchase orders?|po|rfq|work orders?|award letters?|nda|templates?|"
            r"comparative statements?)\b",
            re.I,
        ),
        (
            "draft_document",
            "list_template_fields",
            "read_document",
            "build_spreadsheet",
        ),
    ),
    "tables": (
        re.compile(
            r"\b(tables?|spreadsheets?|sheets?|csv|excel|xlsx|columns?|rows?|"
            r"rank|ranking|export)\b",
            re.I,
        ),
        (
            "describe_table",
            "query_table",
            "rank_table",
            "export_table",
            "build_spreadsheet",
            "save_report",
        ),
    ),
    "registers": (
        re.compile(
            r"\b(registers?|invoices?|gst|ledger|reconcile|reconciliation|"
            r"three[- ]way|delivered|received|vendor bills?|due dates?)\b",
            re.I,
        ),
        (
            "update_register",
            "list_register",
            "match_invoice",
            "save_email_attachment",
            "read_document",
        ),
    ),
    "maturity": (
        re.compile(r"\b(maturity|maturity score|assessment|roadmap)\b", re.I),
        ("assess_maturity",),
    ),
    "reports": (
        re.compile(r"\b(reports?|dashboard|summary of the week)\b", re.I),
        ("save_report",),
    ),
    "trackers": (
        re.compile(r"\b(trackers?|tracking|track (this|these|my))\b", re.I),
        ("create_tracker", "add_to_tracker", "read_tracker"),
    ),
    "commitments": (
        re.compile(
            r"\b(promised?|promises|committed|commitments?|owes?|owed|"
            r"follow(ed)?[- ]?ups?|chase)\b",
            re.I,
        ),
        ("track_commitment", "who_owes_me", "follow_up_commitment"),
    ),
    "learning": (
        re.compile(
            r"\b(course|lesson|teach me|learn|study|tutorial|how do i learn)\b", re.I
        ),
        ("start_course",),
    ),
    "browser": (
        re.compile(
            r"\b(browse|website|web ?page|open the site|go to the site|click|"
            r"log ?in to|sign ?in to|fill (in|out) the form)\b",
            re.I,
        ),
        ("browse",),
    ),
    "scams": (
        re.compile(
            r"\b(scams?|fraud|suspicious|phishing|fake|is this real|otp)\b", re.I
        ),
        ("check_for_scam",),
    ),
    "phone_help": (
        re.compile(
            r"\b(how do i|can'?t (find|open)|set ?up my phone|my phone|"
            r"the app on my phone|settings on)\b",
            re.I,
        ),
        ("phone_help_start", "phone_help_answer"),
    ),
    "medicine": (
        re.compile(r"\b(medicines?|medications?|pills?|tablets?|dose|dosage)\b", re.I),
        ("set_medicine_reminder",),
    ),
    "booking": (
        re.compile(
            r"\b(book(ing|ings)?|appointments?|reservations?|slots?|"
            r"availability)\b",
            re.I,
        ),
        ("set_up_booking",),
    ),
    "calls": (
        re.compile(r"\b(call|phone|ring|dial|buzz)\b", re.I),
        ("call_for_me", "call_me_when_done"),
    ),
    "repository": (
        re.compile(r"\b(github|repository|repo|install (the )?skills?)\b", re.I),
        ("install_from_repository",),
    ),
    "trading": (
        re.compile(
            r"\b(trad(e|ing)|buyers?|suppliers?|importers?|exporters?|hs code)\b",
            re.I,
        ),
        ("trading_interests",),
    ),
    "code": (
        re.compile(
            r"\b(scripts?|python|bulk|thousands of|hundreds of|every row|"
            r"all the rows|for each of)\b",
            re.I,
        ),
        ("run_script",),
    ),
    "ordering": (
        re.compile(
            r"\b(order|buy|purchase|cheapest|cheaper|price|prices|pricing|"
            r"compare|comparison|vendor quotes?)\b",
            re.I,
        ),
        ("order_search", "order_prepare", "compare_prices"),
    ),
    "outside_tools": (
        re.compile(r"\b(connect|integrate|integration|plug in|hook up)\b", re.I),
        ("connect_outside_tool",),
    ),
    "attachments": (
        re.compile(r"\b(attach(ed|ment)s?)\b", re.I),
        ("save_email_attachment", "read_document", "file_document"),
    ),
}


def flagged(text: str) -> frozenset[str]:
    """The tool names this message points at by intent. Loose on purpose."""
    body = text or ""
    found: set[str] = set()
    for pattern, tools in INTENTS.values():
        if pattern.search(body):
            found.update(tools)
    return frozenset(found)


def _words(name: str) -> str:
    return re.sub(r"[_\W]+", " ", name).strip().lower()


def named(text: str, names: Iterable[str]) -> frozenset[str]:
    """The tools the message names: ``check_bot`` or "check bot", as a whole
    word, in any case. Names under four characters are not matched on their
    spaced form, so a tool called ``po`` cannot be summoned by a word."""
    body = (text or "").lower()
    spaced = f" {_words(body)} "
    found: set[str] = set()
    for name in names:
        if not name:
            continue
        if name.lower() in body:
            found.add(name)
            continue
        words = _words(name)
        if len(words) >= 4 and f" {words} " in spaced:
            found.add(name)
    return frozenset(found)


def asked_for(text: str, names: Iterable[str]) -> frozenset[str]:
    """Both ways: by name and by intent, restricted to ``names``."""
    available = set(names)
    return named(text, available) | (flagged(text) & available)


__all__ = ["INTENTS", "asked_for", "flagged", "named"]
