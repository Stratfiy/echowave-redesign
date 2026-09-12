"""What kind of business this is, learned from what it hired.

Every pack declares the industries it serves -- Clinics, Dental, E-commerce,
Logistics, Lending, Edtech. The organization declared nothing, so the same
question got asked on every visit: somebody says "Sunrise Dental in Bandra",
hires a front desk, comes back next month for a recalls agent, and the chat
asks what business they are in from scratch. Worse, ``suggest_roles`` ranked on
the words typed that minute rather than on what the account demonstrably is.

**Learned from an action, not from a form.** Hiring a clinic front desk says
more about a business than any dropdown would, and it costs the operator
nothing. Nobody is asked a new onboarding question.

**And said out loud, never assumed silently.** A guess that is wrong and
invisible is the failure this repository keeps finding; a guess that is wrong
and stated gets corrected in one word. So this returns what it inferred *and*
how sure it is, and the chat is told to say it back.

**Narrow rather than pick.** A pack serving four industries does not tell us
which of the four, so nothing here chooses one -- ``front_desk_clinic`` leaves
all of Clinics, Dental, Diagnostics and Salons standing. Hire a second pack and
the overlap narrows on its own. Picking arbitrarily would file a salon as a
clinic and read as fact forever after.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from loguru import logger

#: How it was arrived at, because the sentence the chat says differs by source.
#: An operator's own answer is stated as fact; an inference is offered for
#: correction; nothing known is not mentioned at all.
SOURCE_SET = "set"
SOURCE_HIRED = "hired"
SOURCE_UNKNOWN = "unknown"


def industries_for_templates(template_ids: Iterable[str]) -> tuple[str, ...]:
    """The industries the templates this account hired could belong to.

    The intersection when packs agree, because two packs sharing one industry
    is a much stronger signal than either alone. The union when they do not,
    since a business running both a clinic front desk and an order-confirmation
    agent genuinely spans two, and an empty intersection would throw away what
    we know rather than report it.
    """
    from api.services.packs.catalogue import all_packs

    wanted = {t for t in template_ids if t}
    if not wanted:
        return ()

    sets: list[set[str]] = []
    for pack in all_packs():
        if pack.template_id in wanted and pack.industries:
            sets.append(set(pack.industries))

    if not sets:
        return ()

    overlap = set.intersection(*sets)
    chosen = overlap or set().union(*sets)

    # Ordered by the catalogue's own order rather than alphabetically, so the
    # industry a pack names first stays first -- it is the one the pack was
    # written for.
    order: list[str] = []
    for pack in all_packs():
        for industry in pack.industries:
            if industry in chosen and industry not in order:
                order.append(industry)
    return tuple(order)


async def resolve(organization_id: Optional[int]) -> dict[str, Any]:
    """What we know about this account's industry, and how we know it.

    Never raises and never blocks: this decorates a chat reply and a ranking,
    and neither is worth failing a conversation over. Anything unreadable comes
    back as "unknown", which is the same state as a brand new account.
    """
    if not organization_id:
        return {"industry": None, "candidates": (), "source": SOURCE_UNKNOWN}

    # An operator's own answer wins over anything inferred. They corrected us,
    # or they told us before we could guess, and either way re-deriving it
    # every turn would quietly overwrite the correction.
    try:
        from api.services.organization_preferences import get_organization_preferences

        preferences = await get_organization_preferences(organization_id)
        stated = (getattr(preferences, "industry", None) or "").strip()
        if stated:
            return {
                "industry": stated,
                "candidates": (stated,),
                "source": SOURCE_SET,
            }
    except Exception as exc:  # noqa: BLE001 - a ranking hint must not end a chat
        logger.warning("Could not read the organization's industry: {}", exc)

    try:
        from api.db import db_client

        template_ids = await db_client.hired_template_ids(organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read hired templates for the industry: {}", exc)
        return {"industry": None, "candidates": (), "source": SOURCE_UNKNOWN}

    candidates = industries_for_templates(template_ids)
    if not candidates:
        return {"industry": None, "candidates": (), "source": SOURCE_UNKNOWN}

    return {
        # Deliberately None while this is an inference. A single candidate is
        # still a guess, and writing it into `industry` would turn "probably a
        # clinic" into "is a clinic" without anybody agreeing to it.
        "industry": None,
        "candidates": candidates,
        "source": SOURCE_HIRED,
    }


def rank_key(known: dict[str, Any]):
    """A sort key that floats packs matching this account's industry.

    A key rather than a filter, on purpose. A clinic asking about payments
    should see the payment-chasing role, ranked below its clinic roles but
    present -- filtering it out would answer "we do not do that", which is
    false and is the silent-absence failure in a new costume.
    """
    candidates = {c.lower() for c in (known.get("candidates") or ())}

    def key(pack: Any) -> tuple[int, str]:
        if not candidates:
            return (0, "")
        shared = candidates & {i.lower() for i in (pack.industries or [])}
        # Negated so more shared industries sort first under an ascending sort.
        return (-len(shared), pack.name.lower())

    return key


def sentence(known: dict[str, Any]) -> Optional[str]:
    """One line for the chat to say, or None when there is nothing to say.

    The point of the whole module: an inference the customer can hear and
    correct. Phrased as a question when it is a guess and as a statement when
    they told us, because "you're a clinic, right?" to somebody who typed
    "clinic" a moment ago reads as not listening.
    """
    source = known.get("source")
    candidates = known.get("candidates") or ()
    if source == SOURCE_SET and known.get("industry"):
        return f"You're set up as {known['industry']}."
    if source != SOURCE_HIRED or not candidates:
        return None

    if len(candidates) == 1:
        return (
            f"From what you've hired I'm assuming {candidates[0]} — "
            "tell me if that's wrong."
        )
    listed = ", ".join(candidates[:3])
    return f"From what you've hired this looks like {listed} — tell me if that's wrong."
