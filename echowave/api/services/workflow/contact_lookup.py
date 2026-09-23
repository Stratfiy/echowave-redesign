"""The people this account knows, when the question names one.

Contacts were reachable from exactly one place: a ringing phone. The
inbound path looks a caller up by number and preloads what it finds into
the run's ``initial_context``. Everywhere else -- and in particular in the
chat, where somebody types "what do we know about Ravi" -- the contact book
did not exist. Decibyl would answer from memory, documents and the timeline
and never once look at the table with the answer in it.

So contacts join the knowledge the assistant reads, which is where a person
already thinks they are: to an operator, "what we know about a customer" is
one idea, not a table and a file store with different rules.

**Matched, not dumped.** An account can hold fifty thousand contacts and a
prompt can hold none of them. Terms are taken from the question and matched
against the name and both spellings of the number; a question naming nobody
reads nobody. That is also why this is a lookup and not a search of the
embeddings: a name is an exact thing, and "Ravi" should find Ravi rather
than the passage most semantically like him. It works on an account with no
embeddings configured at all, which the document search does not.

Never raises. A contact book that cannot be read is a prompt without one,
the state every prompt was in before this existed.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from api.db import db_client

#: How many contacts one question may put in front of the model.
#:
#: Five because a question naming a person means one person, and a term
#: loose enough to match more than five ("call") has matched on noise.
MAX_CONTACTS = 5

#: How many terms are taken from one question.
#:
#: A question is not a query, and every extra term is another wildcard scan
#: of a table that may hold tens of thousands of rows.
MAX_TERMS = 4

#: The shortest term worth matching. Two letters match half a contact book.
MIN_TERM = 3

#: Words that are long enough to look like a name and never are.
#:
#: Deliberately short, and it does not have to be complete. A noise word
#: that slips through matches no contact and costs one more clause in one
#: bounded query; a real name wrongly excluded costs the answer, silently,
#: and nothing on any screen would say why. So this errs towards letting
#: words through -- the same reason the codebase prefers blocklists.
_STOPWORDS = frozenset(
    """
    the and for who what when where which how why did does are was were
    have has had will would could should can may might must about from
    with that this these those there their they them then than our your
    his her its out any all one two some more most been being not but
    you i'm ive dont doesnt cant wont call calls called calling send sent
    text message messages number numbers contact contacts customer customers
    client clients name names phone agent agents agent agents today yesterday
    tomorrow week month year please tell show find give know knows anything
    everything something someone anyone whats what's who's whos
    """.split()
)

#: A phone number as people write it, separators and all: ``+91 98765
#: 43210``, ``98765-43210``, ``(044) 2233 4455``. Matched whole and then
#: reduced to digits, because splitting on the spaces first finds ``98765``
#: and ``43210`` and looks up neither of them.
_PHONE = re.compile(r"[+(]?\d[\d\s().-]{5,}\d")
_WORDS = re.compile(r"[A-Za-z']+")

#: The fewest digits that can be a phone number rather than a quantity or a
#: date. Eight rather than ten: a local number written without its code is
#: still a number somebody may have uploaded.
MIN_DIGITS = 8


def terms(question: str) -> list[str]:
    """The parts of a question worth matching a contact against.

    Numbers first: a question containing a phone number is almost always
    about that number, and a digit run cannot be a stopword. Then words,
    in the order they were said, minus the ones that are never names.

    Deliberately not clever. Recognising which word in "did Ravi Kumar from
    Coimbatore call" is the surname is a problem we do not have to solve --
    matching all three and letting the table decide costs one query.
    """
    if not question:
        return []
    found: list[str] = []
    seen: set[str] = set()

    for run in _PHONE.findall(question):
        digits = re.sub(r"\D", "", run)
        if len(digits) < MIN_DIGITS:
            continue
        # The last ten digits: an account may hold 9876543210 while the
        # person typed +91 98765 43210, and the tail is what they share.
        tail = digits[-10:]
        if tail not in seen:
            seen.add(tail)
            found.append(tail)

    for word in _WORDS.findall(question):
        if len(found) >= MAX_TERMS:
            break
        lowered = word.lower()
        if len(lowered) < MIN_TERM or lowered in _STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        found.append(word)

    return found[:MAX_TERMS]


def _line(contact: Any) -> str:
    """One contact as a line, with whatever the account chose to keep.

    ``attributes`` is open by design -- a policy number, a due date, the
    branch -- so it is rendered rather than enumerated. Truncated per value
    because an account that uploaded a paragraph into a column should not
    be able to spend the prompt on it.
    """
    name = (getattr(contact, "name", None) or "").strip() or "Unnamed"
    phone = (
        getattr(contact, "phone_raw", None)
        or getattr(contact, "phone_normalized", None)
        or ""
    ).strip()
    email = (getattr(contact, "email", None) or "").strip()
    reach = ", ".join(x for x in (phone, email) if x)
    parts = [f"{name} ({reach})" if reach else name]
    attributes = getattr(contact, "attributes", None)
    if isinstance(attributes, dict):
        for key, value in list(attributes.items())[:6]:
            text = str(value).strip()
            if text:
                parts.append(f"{key}: {text[:80]}")
    return "- " + ", ".join(parts)


def block(contacts: list[Any]) -> str:
    """The matched contacts as the model reads them."""
    lines = [_line(c) for c in contacts[:MAX_CONTACTS]]
    return "\n".join(lines)


async def matching(organization_id: int, question: str) -> list[Any]:
    """The account's contacts this question names, if any.

    Never raises: the contact book is one reading of several, and a
    workspace whose contacts cannot be read still gets an answer.
    """
    wanted = terms(question)
    if not wanted:
        return []
    try:
        return await db_client.search_contacts_for_organization(
            organization_id, wanted, limit=MAX_CONTACTS
        )
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning(
            "Could not read the contacts for org {}: {}", organization_id, exc
        )
        return []
