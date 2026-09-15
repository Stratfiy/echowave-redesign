"""The skills a business can teach its bots, as files we ship.

A portable skill is a procedure, not a capability: how to chase an unpaid
invoice, what to check before promising a delivery date, how to run a
pipeline review. It changes how the bot behaves. The connector skill beside
it -- an app and an action slug -- is what the procedure calls. See
``services/skills/__init__.py`` for why the two share a word and nothing else.

These are files on disk rather than rows in a table on purpose. The
catalogue is the same for every account, it ships with the release, and a
skill somebody edited in production is a skill nobody can reproduce. What an
account *does* with one -- installing it, putting it on a bot -- is a row.

Curated from two MIT repositories rather than written here: a closed set of
seven skills we wrote is a feature, an open format is an ecosystem, and the
format already exists. Each file keeps its `source` and `license` in
frontmatter, and :func:`attributions` is what the screen shows.
"""

from __future__ import annotations

import functools
import pathlib
from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.services.skills.document import PortableSkill, SkillParseError, parse

#: Where the shipped files live, beside this module.
CATALOGUE_DIR = pathlib.Path(__file__).parent / "catalogue"


@dataclass(frozen=True)
class CatalogueSkill:
    """One shipped skill, as a card and as a prompt."""

    slug: str
    title: str
    description: str
    division: str
    emoji: str
    source: str
    license: str
    skill: PortableSkill

    def as_card(self) -> dict[str, Any]:
        """What the shelf shows. Deliberately without the body: a list of a
        hundred skills is a megabyte of prompt text nobody is reading yet."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "division": self.division,
            "emoji": self.emoji,
            "source": self.source,
            "license": self.license,
            "lines": self.skill.line_count,
        }


def _load() -> dict[str, CatalogueSkill]:
    out: dict[str, CatalogueSkill] = {}
    if not CATALOGUE_DIR.is_dir():
        logger.warning("No skills catalogue at {}", CATALOGUE_DIR)
        return out
    for path in sorted(CATALOGUE_DIR.glob("*.md")):
        try:
            skill = parse(path.read_text(encoding="utf-8"), source=path.name)
        except (SkillParseError, OSError) as exc:
            # One malformed file is not a broken shelf. It is, though, a bug
            # in whatever wrote it, so it is loud in the log.
            logger.error("Skipping skill {}: {}", path.name, exc)
            continue
        meta = skill.metadata
        out[skill.name] = CatalogueSkill(
            slug=skill.name,
            title=str(meta.get("title") or skill.name.replace("-", " ").title()),
            description=skill.description,
            division=str(meta.get("division") or "Other"),
            emoji=str(meta.get("emoji") or ""),
            source=str(meta.get("source") or ""),
            license=str(meta.get("license") or ""),
            skill=skill,
        )
    return out


@functools.lru_cache(maxsize=1)
def _cached() -> dict[str, CatalogueSkill]:
    skills = _load()
    logger.info("Skills catalogue: {} skills", len(skills))
    return skills


def all_skills() -> dict[str, CatalogueSkill]:
    """Every shipped skill, by slug. Read once per process."""
    return _cached()


def get(slug: str) -> CatalogueSkill | None:
    return all_skills().get((slug or "").strip().lower())


def divisions() -> list[str]:
    """The headings, in the order the shelf shows them: most skills first, so
    a business scanning for its own work sees the big shelves before the
    thin ones."""
    counts: dict[str, int] = {}
    for skill in all_skills().values():
        counts[skill.division] = counts.get(skill.division, 0) + 1
    return [d for d, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def attributions() -> list[dict[str, str]]:
    """Who wrote what is on this shelf, for the credit line."""
    seen: dict[str, str] = {}
    for skill in all_skills().values():
        if skill.source:
            seen[skill.source] = skill.license
    return [
        {"source": source, "license": licence}
        for source, licence in sorted(seen.items())
    ]


def forget() -> None:
    """Drop the cache. For tests, and for a reload after a release."""
    _cached.cache_clear()
