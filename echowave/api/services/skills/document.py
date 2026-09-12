"""Reading a ``SKILL.md``, and saying plainly what is in it.

The format is the published Agent Skills one, unchanged on purpose: YAML
frontmatter carrying ``name`` and ``description``, then a markdown body, with
the spec's own guidance that the body stay under 500 lines. Taking it as-is is
the whole point -- a skill somebody wrote for Claude, or found in a
repository, should work here without being rewritten.

What this module will not do is trust it.

An imported skill is **text from a stranger that ends up in the prompt of a
bot answering somebody's phone**. Three things follow, and only the first is
in this file:

1. *It is inspected, and what is worth a human's attention is named.* That is
   :func:`concerns` below. It is a **review aid, not a security control**, and
   calling it one would be the dishonest version -- a list of phrases cannot
   be a boundary against text written to get around a list of phrases.
2. *A person reviews it before it reaches a live bot.* Same posture as
   ``organisation_facts``, where a learned fact never reaches a prompt until
   somebody says yes, for the same reason: a model confidently telling real
   callers something nobody approved.
3. *A skill may add capability but never identity.* Decibyl keeps the persona
   in its own ``globalNode``, "applied on top of the prompt" -- so a skill is
   injected as procedure underneath it and the persona wins by construction
   rather than by hope. That is a property of the graph, not of this parser,
   and it is why this parser is allowed to be as permissive as it is.

Nothing here is silently altered or dropped. A skill whose body tries to
rename the bot is parsed, flagged and shown to the reviewer intact, because a
skill that behaves differently from what its author wrote is a worse problem
than one that needed a second look: the operator would be reading a file that
is not what is running.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml
from loguru import logger

#: The spec's own guidance: "Keep SKILL.md under 500 lines for optimal agent
#: performance." Enforced rather than suggested, because the body goes into a
#: prompt on a metered call -- an unbounded one is somebody else's text
#: costing our customer money on every turn.
MAX_BODY_LINES = 500

#: Frontmatter sizes. Generous, and bounded: these two strings are shown on a
#: card and read by a model, and neither reads well at a thousand characters.
MAX_NAME = 64
MAX_DESCRIPTION = 1_024

#: ``name`` per the spec: a unique lowercase identifier, hyphens for spaces.
#: Digits and underscores allowed because published skills use them and
#: refusing a real file over punctuation would be us inventing a dialect.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class SkillParseError(ValueError):
    """The file is not a skill. Raised with a sentence a person can act on."""


@dataclass(frozen=True)
class Concern:
    """Something in the body a reviewer should look at before approving.

    Carries the line so the reviewer is taken to it rather than told to search
    a five-hundred-line file.
    """

    #: Short machine-readable kind, for grouping in the UI.
    kind: str
    #: One line, in the words of what it would do.
    detail: str
    #: 1-indexed line of the body.
    line: int
    #: The matched text, truncated. Shown so the reviewer judges the thing
    #: itself rather than our description of it.
    excerpt: str


@dataclass(frozen=True)
class PortableSkill:
    """One ``SKILL.md``, parsed."""

    name: str
    description: str
    body: str
    #: Every other frontmatter key, kept rather than dropped. Publishers are
    #: already putting `license`, `version` and `allowed-tools` in these files;
    #: discarding what we do not yet read would lose information the next
    #: version of this product wants, and an unknown key is not an error.
    metadata: dict[str, Any] = field(default_factory=dict)
    concerns: tuple[Concern, ...] = ()

    @property
    def needs_review(self) -> bool:
        """Whether a person must look at this before a bot runs it.

        True for everything imported, always -- not only when a concern was
        found. The concerns list is a reviewer's shortcut, and treating an
        empty one as a pass would make the review depend on our pattern list
        being complete, which it is not and cannot be.
        """
        return True

    @property
    def line_count(self) -> int:
        return len(self.body.splitlines())


#: What is worth a reviewer's attention, and why. Each entry is a pattern, a
#: kind, and the sentence shown beside the match.
#:
#: Not a blocklist -- nothing here refuses a skill. The point is that a
#: reviewer approving a hundred-line file from a repository gets pointed at
#: the four lines that matter instead of reading all hundred with equal care.
_CONCERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\b(?:you are|your name is|you're)\b", re.IGNORECASE),
        "identity",
        "Tries to set who the bot is. The bot's persona is separate and wins, "
        "so this will not take effect -- but it means the skill was written "
        "for a different assistant.",
    ),
    (
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?"
            r"(?:previous|prior|earlier|above|preceding)\b",
            re.IGNORECASE,
        ),
        "override",
        "Tries to discard the instructions around it.",
    ),
    (
        re.compile(
            r"\b(?:api[_ -]?key|secret[_ -]?key|password|credential|"
            r"access[_ -]?token|bearer\s+token|private[_ -]?key)\b",
            re.IGNORECASE,
        ),
        "secrets",
        "Mentions credentials. A skill never needs one -- apps are connected "
        "separately and a skill is only told which to use.",
    ),
    (
        re.compile(
            r"\b(?:send|post|upload|forward|exfiltrate)\b[^.\n]{0,60}"
            r"\b(?:https?://|webhook|endpoint)\b",
            re.IGNORECASE,
        ),
        "outbound",
        "Sends data to an address written into the skill rather than to an "
        "app the operator connected.",
    ),
    (
        re.compile(
            r"\b(?:all|every|entire|full)\s+"
            r"(?:customers?|contacts?|patients?|records?|database|"
            r"call list|phone numbers?)\b",
            re.IGNORECASE,
        ),
        "bulk-data",
        "Refers to the whole customer list. Worth checking against what this "
        "bot is meant to do on one call.",
    ),
)

#: How much of a matched line is shown. Enough to judge, short enough for a
#: card.
_EXCERPT = 160


def concerns(body: str) -> tuple[Concern, ...]:
    """Lines of ``body`` a reviewer should read first.

    Ordered by line so the reviewer walks the file top to bottom rather than
    by our idea of severity, which would be a judgement we are not in a
    position to make about somebody else's business.
    """
    found: list[Concern] = []
    for index, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, kind, detail in _CONCERNS:
            match = pattern.search(stripped)
            if match:
                found.append(
                    Concern(
                        kind=kind,
                        detail=detail,
                        line=index,
                        excerpt=stripped[:_EXCERPT],
                    )
                )
    return tuple(found)


def parse(text: str, *, source: str = "SKILL.md") -> PortableSkill:
    """Read one ``SKILL.md``.

    Raises :class:`SkillParseError` with a sentence naming what to fix. Every
    refusal here is a malformed file rather than a judgement about content: a
    skill we dislike is a skill a reviewer declines, not one the parser
    rejects on their behalf.
    """
    if not isinstance(text, str) or not text.strip():
        raise SkillParseError(f"{source} is empty.")

    match = _FRONTMATTER.match(text.lstrip("﻿"))
    if match is None:
        raise SkillParseError(
            f"{source} has no frontmatter. A skill starts with a '---' block "
            "carrying at least a name and a description."
        )

    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillParseError(f"{source} frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(loaded, dict):
        raise SkillParseError(
            f"{source} frontmatter must be a block of key: value pairs."
        )

    metadata = {str(key): value for key, value in loaded.items()}
    name = str(metadata.pop("name", "") or "").strip()
    description = str(metadata.pop("description", "") or "").strip()

    if not name:
        raise SkillParseError(f"{source} has no name in its frontmatter.")
    if len(name) > MAX_NAME:
        raise SkillParseError(
            f"{source} has a name of {len(name)} characters; the limit is {MAX_NAME}."
        )
    if not _NAME.match(name):
        raise SkillParseError(
            f"{source} has the name {name!r}. A skill name is lowercase, with "
            "hyphens instead of spaces."
        )
    if not description:
        # The field the model reads to decide whether to use the skill at all.
        # A skill with no description is a skill that never fires, which is
        # the silent kind of broken.
        raise SkillParseError(
            f"{source} has no description. It is what tells the bot when to "
            "use the skill, so without it the skill never runs."
        )
    if len(description) > MAX_DESCRIPTION:
        raise SkillParseError(
            f"{source} has a description of {len(description)} characters; "
            f"the limit is {MAX_DESCRIPTION}."
        )

    body = text[match.end() :].strip("\n")
    if not body.strip():
        raise SkillParseError(f"{source} has frontmatter but no instructions under it.")

    lines = len(body.splitlines())
    if lines > MAX_BODY_LINES:
        raise SkillParseError(
            f"{source} is {lines} lines; the limit is {MAX_BODY_LINES}. The "
            "body goes into the prompt on every turn of a metered call, so a "
            "long one costs money on calls that never use it."
        )

    return PortableSkill(
        name=name,
        description=description,
        body=body,
        metadata=metadata,
        concerns=concerns(body),
    )


def parse_many(
    files: Iterable[tuple[str, str]],
) -> tuple[tuple[PortableSkill, ...], tuple[tuple[str, str], ...]]:
    """Parse a folder's worth, returning what worked and what did not.

    Two lists rather than raising, because importing a repository of thirty
    skills where one is malformed should add twenty-nine and say which one was
    skipped. Failing the batch would make one bad file hide the rest; dropping
    it silently would be the same absence this codebase keeps getting caught
    by.
    """
    parsed: list[PortableSkill] = []
    failed: list[tuple[str, str]] = []
    for source, text in files:
        try:
            parsed.append(parse(text, source=source))
        except SkillParseError as exc:
            logger.info("Skipping {}: {}", source, exc)
            failed.append((source, str(exc)))
    return tuple(parsed), tuple(failed)


def prompt_block(skill: PortableSkill) -> str:
    """The skill as it is injected, delimited and labelled.

    Fenced and named so the model can tell where somebody else's text starts
    and stops. This is not a security boundary either -- the boundaries are
    the person who reviewed it, the persona node that sits above it, and the
    fact that tools are attached separately and a skill naming an action does
    not receive it.
    """
    return (
        f'<skill name="{skill.name}">\n'
        f"Use this when: {skill.description}\n\n"
        f"{skill.body}\n"
        "</skill>"
    )


__all__ = [
    "MAX_BODY_LINES",
    "MAX_DESCRIPTION",
    "MAX_NAME",
    "Concern",
    "PortableSkill",
    "SkillParseError",
    "concerns",
    "parse",
    "parse_many",
    "prompt_block",
]
