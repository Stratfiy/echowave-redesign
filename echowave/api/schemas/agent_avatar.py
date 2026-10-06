"""An agent's face: which body shape, colour and resting expression it wears.

The face itself is drawn in the browser (ui/src/lib/bloub, after
github.com/jeremy-prt/bloub, MIT); the server only stores the three choices so
every screen and every teammate sees the same agent. The ids are the
customiser's own, kept in step with ``ui/src/lib/bloub/skins.ts`` and
``expressions.ts``: an id the browser does not know would draw the default
face and say nothing, so an unknown one is refused here instead.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

AvatarShape = Literal[
    "cercle",
    "galet",
    "squircle",
    "capsule",
    "triangle",
    "hexagone",
    "nuage",
    "goutte",
]

AvatarColor = Literal[
    "encre",
    "creme",
    "brun",
    "rouge",
    "orange",
    "ambre",
    "vert",
    "turquoise",
    "bleu",
    "violet",
    "rose",
    "gris",
]

AvatarExpression = Literal[
    "neutre",
    "attentif",
    "surpris",
    "excite",
    "heureux",
    "hilare",
    "colere",
    "triste",
    "effraye",
    "mefiant",
    "confus",
    "curieux",
    "fier",
    "timide",
    "blase",
    "somnolent",
]


class AgentAvatar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: AvatarShape = "cercle"
    color: AvatarColor = "encre"
    expression: AvatarExpression = "neutre"


def read_avatar(value: object) -> AgentAvatar | None:
    """The stored JSON as an avatar, or None when unset or no longer valid.

    A row written before an id was renamed must not break the agent list, so a
    value that no longer validates reads as "no avatar" (the default face).
    """
    if not isinstance(value, dict):
        return None
    try:
        return AgentAvatar.model_validate(value)
    except ValueError:
        return None
