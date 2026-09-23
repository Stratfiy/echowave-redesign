"""The telecaller call coach's shelf entry (CR-3).

``requires_feature="dialer_import"``: listed only while the dialer import is
on -- listed before, it could be hired and would sit reading an empty list
of calls. It goes on the shelf by itself when the import is switched on,
which waits on the founder approving the connect screen's consent wording.
"""

from __future__ import annotations

from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    RequiredFact,
)
from api.services.packs.chat_desks import _BUSINESS_NAME, _SHEET, _WHATSAPP


def packs(publisher) -> tuple[AgentPack, ...]:
    return (
        AgentPack(
            slug="telecaller_call_coach",
            name="Human telecaller call coach",
            job="Team lead / quality analyst",
            summary=(
                "Reads the day's recorded calls from your dialer, sends each "
                "telecaller a private two-line note every evening, and sends "
                "you one board for the whole team each week."
            ),
            publisher=publisher,
            channels=[Channel.SCHEDULED, Channel.WHATSAPP],
            template_id="telecaller_call_coach",
            languages=["en", "hi", "ta", "te", "kn", "mr"],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="team_roster",
                    question="Who is on your telecalling team, and which number does each use?",
                    kind=FactKind.LONG_TEXT,
                    example="Divya 98400 12345; Arun 98400 67890",
                    used_for="Naming each caller in their note -- dialers know numbers, not names.",
                ),
                RequiredFact(
                    key="owner_contact",
                    question="Who should get the weekly board?",
                    example="Vikram, on WhatsApp +91 98400 11111",
                    used_for="The weekly board, and any call that needs attention the same day.",
                ),
                RequiredFact(
                    key="board_day",
                    question="Which day should the weekly board arrive?",
                    example="Saturday",
                    used_for="When the week's scores go to the owner.",
                ),
                RequiredFact(
                    key="register_sheet",
                    question="Where do callers write each call's outcome?",
                    example="The Call register sheet",
                    required=False,
                    used_for="Checking each call was written up. Skip it if you keep no register.",
                ),
            ],
            # The dialer is not a connector here: it is connected under
            # Integrations -> Dialer, and the calls reach the coach through
            # its built-in tool, not an app.
            required_connectors=[_WHATSAPP, _SHEET],
            requires_feature="dialer_import",
        ),
    )


__all__ = ["packs"]
