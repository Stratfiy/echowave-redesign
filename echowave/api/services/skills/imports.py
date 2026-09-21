"""Install skills from a repository link, on the thread (D-1b).

"msitarzewski/agency-agents, install them" is the whole ask. What it takes:
fetch the repository, recognise what is in it, and install what can be
installed, with a card a person confirms in between -- installing is a
write to the workspace, and the rule for writes is a card.

**Fetch.** A GitHub link (``owner/repo``, a page URL, a ``tree/<ref>/<path>``
URL) becomes one tarball from codeload, read in memory, capped in size and
in file count. Nothing is cloned to disk and nothing is run.

**Recognise.** Two shapes are read. A *pack folder* -- ``SKILL.md`` with a
``decibyl`` block, our own published layout -- is reported but not
installed here: a pack registers a template into the process, which is a
deployment's decision (``packs/folder.py``), not a workspace's. A *skill
file* -- any ``.md`` with frontmatter carrying a name and a description --
is what installs. Published role packs write ``name: Incident Response
Commander`` where the skills spec wants a slug, so the reader is lenient
about the name and strict about everything else: the slug comes from the
file name, the title from the frontmatter, and a file over the line limit
or with no description is skipped and *named* in the report, never dropped.

**Install.** Each skill becomes an ``organisation_skill_documents`` row,
body and all, plus the same ``organisation_skills`` row a shipped skill gets,
so it sits on the shelf, attaches to a bot and uninstalls like any other.
Every one needs a review before a bot runs it -- the parser flags concerns,
an empty list is not a pass -- and the card says so.
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger

from api.db import db_client
from api.services.skills.document import (
    PortableSkill,
    SkillParseError,
    parse,
)

TOOL_NAME = "install_from_repository"

#: A tarball bigger than this is not a skills repository.
MAX_ARCHIVE_BYTES = 25_000_000
MAX_FILE_BYTES = 200_000
MAX_FILES = 400
#: How many skills one import may install. A repository of three hundred
#: roles is a shelf nobody can read; the person names what they want.
MAX_INSTALL = 60
TIMEOUT_SECS = 30.0
USER_AGENT = "DecibylBot/1.0 (+https://decibyl.ai/bot)"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class ImportError_(ValueError):
    """Told to the person in their words."""


@dataclass(frozen=True)
class Link:
    owner: str
    repo: str
    ref: str = "HEAD"
    path: str = ""

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def archive_url(self) -> str:
        return f"https://codeload.github.com/{self.owner}/{self.repo}/tar.gz/{self.ref}"


def parse_link(text: str) -> Link:
    """``owner/repo``, ``https://github.com/owner/repo``, or a
    ``.../tree/<ref>/<path>`` page. Anything else is refused by name."""
    raw = (text or "").strip()
    if not raw:
        raise ImportError_("Say which repository.")
    if "://" not in raw and raw.count("/") == 1 and " " not in raw:
        owner, repo = raw.split("/")
        return Link(owner=owner, repo=_clean_repo(repo))
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parts.hostname not in ("github.com", "www.github.com"):
        raise ImportError_("Only GitHub links are read just now.")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise ImportError_("That link does not name a repository.")
    owner, repo = segments[0], _clean_repo(segments[1])
    ref, path = "HEAD", ""
    if len(segments) >= 4 and segments[2] in ("tree", "blob"):
        ref = segments[3]
        path = "/".join(segments[4:])
    return Link(owner=owner, repo=repo, ref=ref, path=path)


def _clean_repo(repo: str) -> str:
    repo = repo.removesuffix(".git")
    if not re.match(r"^[A-Za-z0-9_.-]+$", repo):
        raise ImportError_("That link does not name a repository.")
    return repo


@dataclass
class Fetched:
    link: Link
    #: Text files by path inside the repository, ``.md`` only.
    files: dict[str, str] = field(default_factory=dict)
    skipped_binary: int = 0


async def fetch(link: Link, *, client: httpx.AsyncClient | None = None) -> Fetched:
    """The repository's markdown, from one tarball read in memory."""
    own = client is None
    http = client or httpx.AsyncClient(
        timeout=TIMEOUT_SECS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        response = await http.get(link.archive_url)
        if response.status_code == 404:
            raise ImportError_(f"{link.name} at {link.ref} was not found on GitHub.")
        response.raise_for_status()
        blob = response.content
    except ImportError_:
        raise
    except Exception as exc:
        logger.warning("Could not fetch {}: {}", link.archive_url, exc)
        raise ImportError_(f"{link.name} could not be fetched just now.") from exc
    finally:
        if own:
            await http.aclose()
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise ImportError_(f"{link.name} is bigger than a skills repository should be.")
    out = Fetched(link=link)
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                # codeload prefixes every path with "<repo>-<sha>/".
                inner = (
                    member.name.split("/", 1)[1] if "/" in member.name else member.name
                )
                if (
                    link.path
                    and not inner.startswith(link.path.rstrip("/") + "/")
                    and inner != link.path
                ):
                    continue
                if not inner.lower().endswith(".md"):
                    out.skipped_binary += 1
                    continue
                if member.size > MAX_FILE_BYTES:
                    continue
                if len(out.files) >= MAX_FILES:
                    break
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                out.files[inner] = handle.read().decode("utf-8", errors="replace")
    except tarfile.TarError as exc:
        raise ImportError_(f"{link.name} did not unpack as a repository.") from exc
    return out


# --- recognise ---------------------------------------------------------------


@dataclass(frozen=True)
class FoundSkill:
    slug: str
    title: str
    path: str
    skill: PortableSkill


@dataclass
class Found:
    skills: list[FoundSkill] = field(default_factory=list)
    #: Pack folders (our own layout): reported, not installed from a thread.
    packs: list[str] = field(default_factory=list)
    #: Files that looked like skills and could not be read, with why.
    skipped: list[tuple[str, str]] = field(default_factory=list)


def slug_for(path: str) -> str:
    stem = path.rsplit("/", 1)[-1].removesuffix(".md").removesuffix(".MD")
    if stem.upper() == "SKILL" and "/" in path:
        stem = path.rsplit("/", 2)[-2]
    slug = _SLUG_STRIP.sub("-", stem.lower()).strip("-")
    return slug[:64] or "skill"


def read_skill(path: str, text: str) -> PortableSkill:
    """``parse``, lenient about the name only.

    A published role file says ``name: Incident Response Commander``. That
    is a title, and the skills spec's slug rule would refuse it; the slug
    is the file's name instead and the title is kept in the metadata. A
    file with no frontmatter, no description or too many lines is refused
    with the parser's own sentence.
    """
    try:
        return parse(text, source=path)
    except SkillParseError as exc:
        if "A skill name is lowercase" not in str(exc) and "name of" not in str(exc):
            raise
    match = re.match(r"^(﻿?---\s*\n)(.*?)(\n---\s*\n?)", text, re.DOTALL)
    if match is None:
        raise SkillParseError(f"{path} has no frontmatter.")
    head = match.group(2)
    title = ""
    kept: list[str] = []
    for line in head.splitlines():
        if re.match(r"^name\s*:", line):
            title = line.split(":", 1)[1].strip().strip("'\"")
            continue
        kept.append(line)
    slug = slug_for(path)
    rebuilt = (
        match.group(1)
        + "\n".join([f"name: {slug}", f"title: {title or slug}", *kept])
        + match.group(3)
        + text[match.end() :]
    )
    return parse(rebuilt, source=path)


def _frontmatter(text: str) -> str | None:
    match = re.match(r"^\ufeff?---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    return match.group(1) if match else None


def is_pack_folder(path: str, text: str) -> bool:
    """Our own published layout: ``SKILL.md`` whose frontmatter carries a
    ``decibyl`` block. Reported, never installed from a thread."""
    if path.rsplit("/", 1)[-1].upper() != "SKILL.MD":
        return False
    head = _frontmatter(text)
    return bool(head) and re.search(r"^decibyl\s*:", head, re.MULTILINE) is not None


def recognise(fetched: Fetched) -> Found:
    found = Found()
    for path in sorted(fetched.files):
        text = fetched.files[path]
        if is_pack_folder(path, text):
            found.packs.append(path.rsplit("/", 1)[0] if "/" in path else ".")
            continue
        if _frontmatter(text) is None:
            # README, LICENSE, CONTRIBUTING: not a skill, not a failure.
            continue
        try:
            skill = read_skill(path, text)
        except SkillParseError as exc:
            found.skipped.append((path, str(exc)))
            continue
        title = str(skill.metadata.get("title") or skill.name.replace("-", " ").title())
        found.skills.append(
            FoundSkill(slug=skill.name, title=title, path=path, skill=skill)
        )
    return found


# --- install -----------------------------------------------------------------


async def install(
    *,
    organization_id: int,
    user_id: int | None,
    link: Link,
    found: Found,
    only: list[str] | None = None,
) -> list[str]:
    """Write the found skills and put them on the shelf. Returns the slugs."""
    wanted = set(only or [])
    done: list[str] = []
    for item in found.skills:
        if wanted and item.slug not in wanted:
            continue
        if len(done) >= MAX_INSTALL:
            break
        skill = item.skill
        await db_client.upsert_skill_document(
            organization_id=organization_id,
            slug=item.slug,
            title=item.title[:200],
            description=skill.description,
            body=skill.body,
            metadata_={k: v for k, v in skill.metadata.items() if _plain(v)},
            source_repo=link.name,
            source_ref=link.ref,
            source_path=item.path,
            licence=str(
                skill.metadata.get("license") or skill.metadata.get("licence") or ""
            )[:64],
            concerns=[str(c) for c in skill.concerns],
            created_by=user_id,
        )
        await db_client.add_organisation_skill(
            organization_id=organization_id,
            slug=item.slug,
            workflow_id=None,
            user_id=user_id,
        )
        done.append(item.slug)
    return done


def _plain(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, list, dict)) or value is None


async def uninstall(*, organization_id: int, slugs: list[str]) -> int:
    """Take imported skills off the shelf and off every bot. Returns how many."""
    count = 0
    for slug in slugs:
        await db_client.remove_organisation_skill(
            organization_id=organization_id, slug=slug, workflow_id=None, all_rows=True
        )
        if await db_client.delete_skill_document(
            organization_id=organization_id, slug=slug
        ):
            count += 1
    return count


# --- the thread ----------------------------------------------------------------


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Install skills from a GitHub repository the person names -- "
            "'owner/repo', a repository page, or a folder in it. Reads the "
            "repository now and proposes a card listing what it would "
            "install; nothing installs until a person confirms. Installed "
            "skills go on the shelf for review; a bot runs one only once "
            "somebody has put it on that bot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "The link or 'owner/repo', as the person gave it.",
                },
                "only": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Skill names to limit the install to, when the person named some.",
                },
            },
            "required": ["repository"],
        },
    }


def summary(found: Found, *, only: list[str] | None = None) -> str:
    wanted = set(only or [])
    names = [s.title for s in found.skills if not wanted or s.slug in wanted]
    parts = [f"{len(names)} skill{'s' if len(names) != 1 else ''}"]
    if found.packs:
        parts.append(
            f"{len(found.packs)} pack folder{'s' if len(found.packs) != 1 else ''} "
            "(a deployment loads those; not from here)"
        )
    if found.skipped:
        parts.append(
            f"{len(found.skipped)} file{'s' if len(found.skipped) != 1 else ''} skipped"
        )
    line = ", ".join(parts)
    if names:
        shown = ", ".join(names[:8]) + (
            f" and {len(names) - 8} more" if len(names) > 8 else ""
        )
        line = f"{line}: {shown}"
    return line


async def for_thread(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool call: read the repository, then propose the install card.
    Never raises."""
    from api.services.workflow import actions

    try:
        link = parse_link(str(arguments.get("repository") or ""))
        fetched = await fetch(link)
    except ImportError_ as exc:
        return {"status": "error", "error": str(exc)}
    found = recognise(fetched)
    only = [str(s) for s in (arguments.get("only") or []) if str(s).strip()]
    if not found.skills or (
        only and not any(s.slug in set(only) for s in found.skills)
    ):
        out: dict[str, Any] = {
            "status": "nothing_to_install",
            "found": summary(found, only=only),
        }
        if found.skipped:
            out["skipped"] = [f"{p}: {why}" for p, why in found.skipped[:10]]
        if found.packs:
            out["packs"] = found.packs[:10]
        return out
    proposal = await actions.propose(
        organization_id=organization_id,
        workflow_id=None,
        workflow_run_id=None,
        arguments={
            "action": actions.INSTALL_FROM_REPOSITORY,
            "repository": link.name,
            "ref": link.ref,
            "path": link.path,
            "slugs": [s.slug for s in found.skills if not only or s.slug in set(only)][
                :MAX_INSTALL
            ],
            "titles": [
                s.title for s in found.skills if not only or s.slug in set(only)
            ][:MAX_INSTALL],
            "why": f"Asked on the thread: install from {link.name}",
        },
        in_channel=False,
    )
    proposal["found"] = summary(found, only=only)
    if found.skipped:
        proposal["skipped"] = [f"{p}: {why}" for p, why in found.skipped[:10]]
    if found.packs:
        proposal["packs"] = found.packs[:10]
    return proposal
