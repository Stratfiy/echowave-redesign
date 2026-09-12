"""Portable skills: a procedure a bot can be taught, as one markdown file.

Kept apart from ``services/integrations/composio/skills.py``, which is a
different thing wearing the same word, and the difference is worth stating
once here so nobody merges them later.

* A **connector skill** is an app and an exact action slug --
  ``email-the-confirmation`` resolving to ``GMAIL_SEND_EMAIL``. It is
  *executable*: attaching it gives a bot something it can do.
* A **portable skill**, this package, is ``SKILL.md``: YAML frontmatter and a
  markdown procedure. It is *not* executable. It changes how the bot behaves.

They compose rather than compete. The portable skill is the procedure -- how to
chase an unpaid invoice, what to check before promising a delivery date -- and
the connector skill is the capability that procedure calls.

The reason to take the file format rather than invent one: a customer who
finds a skill in a repository, or writes one for Claude, should be able to add
it to their bot. A closed set of seven skills we wrote is a feature; an open
format is an ecosystem, and the format already exists and is already what
people are publishing.
"""

from api.services.skills.document import (
    MAX_BODY_LINES,
    Concern,
    PortableSkill,
    SkillParseError,
    parse,
    parse_many,
)

__all__ = [
    "MAX_BODY_LINES",
    "Concern",
    "PortableSkill",
    "SkillParseError",
    "parse",
    "parse_many",
]
