"""A pack as a folder: ``SKILL.md`` plus ``references/`` and ``scripts/``.

The catalogue in ``catalogue.py`` is Python, which is fine for the eight packs
we wrote and wrong for the next hundred. A pack somebody else publishes, a
draft a product person is still writing, a role somebody wants to try from a
git checkout -- none of those should need a code change and a deploy. So a
pack can also be a folder, in the published Agent Skills layout that the
skills parser already reads, and this module turns such a folder into the
same ``AgentPack`` and ``AgentTemplate`` the catalogue produces.

The layout::

    <slug>/
      SKILL.md          frontmatter: name, description, and a ``decibyl`` block
                        body: one ``## <node name>`` section per node, holding
                        that node's prompt
      references/       supporting text a reviewer or a bot may read on demand
      scripts/          listed, never run here

The ``decibyl`` block carries what the skills format has no home for: the
pack declaration (channels, facts, connectors), the template minus its
prompts (stack, edges, guardrails, node names and types), where the folder
came from (``source``: repository, pinned ref, path), and whether it is a
``draft``. Prompts live in the body because that is what a person reviewing a
pack reads, and a reviewer should read the thing that runs.

Two rules keep a folder honest.

*A folder that names a catalogue template must match it.* The catalogue is
what runs. A folder for ``front_desk_clinic`` that quietly differed from the
catalogue would be two truths, and the reviewer would be reading the one that
does not answer the phone. So the loader compares, and drift is an error
naming the field.

*A folder template that is not in the catalogue is registered by id, and
never listed.* It resolves, so the pack validates and the pack can be hired
by whoever loaded it; it does not appear in the template gallery, because a
draft is not a product until somebody promotes it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml
from pydantic import ValidationError

from api.services.agent_templates import AgentTemplate, TemplateNode, get_template
from api.services.agent_templates.catalogue import register_template
from api.services.agent_templates.materialise import to_workflow_definition
from api.services.packs._base import CALLING_CHANNELS, AgentPack, Channel
from api.services.skills.document import PortableSkill, SkillParseError, parse

SKILL_FILE = "SKILL.md"
REFERENCES_DIR = "references"
SCRIPTS_DIR = "scripts"
#: Bumped when the ``decibyl`` block changes shape. A folder written for a
#: newer format fails to load with a sentence rather than half-loading.
FORMAT_VERSION = 1

#: Where this repository keeps its own folders: ``packs/live`` mirrors the
#: catalogue, ``packs/drafts`` holds roles being written.
PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"
LIVE_DIR = PACKS_ROOT / "live"
DRAFTS_DIR = PACKS_ROOT / "drafts"

_SECTION = re.compile(r"^## (.+?)\s*$")
_TITLE = re.compile(r"^# .+$")


class PackFolderError(ValueError):
    """The folder is not a pack. Raised with a sentence a person can act on."""


@dataclass(frozen=True)
class Source:
    """Where the folder came from, pinned so it can be fetched again."""

    repo: str = ""
    #: A tag or commit, never a branch name: a branch moves, and a pack
    #: somebody hired must keep meaning the same thing.
    ref: str = ""
    path: str = ""
    licence: str = ""


@dataclass(frozen=True)
class FolderPack:
    """One folder, loaded: the pack, its template, and what sat beside them."""

    path: Path
    skill: PortableSkill
    pack: AgentPack
    template: AgentTemplate
    source: Source
    draft: bool
    #: ``references/*`` by file name. Read on demand by whoever needs them;
    #: never injected into a prompt by this module.
    references: dict[str, str]
    #: ``scripts/*`` by file name. Listed so a reviewer sees them; nothing
    #: here runs them.
    scripts: tuple[str, ...]

    def workflow_definition(self) -> dict[str, Any]:
        """The worker this folder installs, as the editor and engine read it."""
        return to_workflow_definition(self.template)


# --------------------------------------------------------------------------
# Rendering: pack + template -> SKILL.md text
# --------------------------------------------------------------------------


def skill_name(slug: str) -> str:
    """The skills-format name for a pack slug: hyphens, as the spec wants."""
    return slug.replace("_", "-")


def render(
    pack: AgentPack,
    template: AgentTemplate,
    *,
    source: Source = Source(),
    draft: bool = False,
) -> str:
    """The ``SKILL.md`` text for a pack and the template it wraps.

    The pack's demo line and listing are left out: they are decided by the
    deployment that loads the folder, not by the folder. A draft is written
    with ``listed: false`` so it can never reach a shelf by accident.
    """
    if template.id != pack.template_id:
        raise PackFolderError(
            f"pack {pack.slug!r} wraps template {pack.template_id!r}, "
            f"not {template.id!r}"
        )
    for node in template.nodes:
        for line in node.prompt.splitlines():
            if _SECTION.match(line) or _TITLE.match(line):
                raise PackFolderError(
                    f"template {template.id!r} node {node.name!r} has a line "
                    f"that reads as a heading ({line[:40]!r}); the body uses "
                    "'## ' to separate nodes, so it cannot be written"
                )
        if node.prompt != node.prompt.strip("\n"):
            raise PackFolderError(
                f"template {template.id!r} node {node.name!r} starts or ends "
                "with a blank line, which the body cannot preserve"
            )
    names = [node.name for node in template.nodes]
    if len(names) != len(set(names)):
        raise PackFolderError(
            f"template {template.id!r} has two nodes with one name; body "
            "sections are found by name"
        )

    pack_data = pack.model_dump(
        mode="json",
        exclude_defaults=True,
        exclude={"summary", "demo_number", "demo_url", "listed", "template_id"},
    )
    if draft:
        pack_data["listed"] = False

    template_data = template.model_dump(
        mode="json", exclude_defaults=True, exclude={"suggested_voices", "nodes"}
    )
    template_data["nodes"] = [
        node.model_dump(mode="json", exclude_defaults=True, exclude={"prompt"})
        for node in template.nodes
    ]

    block: dict[str, Any] = {"format": FORMAT_VERSION}
    if draft:
        block["draft"] = True
    pinned = {k: v for k, v in asdict(source).items() if v}
    if pinned:
        block["source"] = pinned
    block["pack"] = pack_data
    block["template"] = template_data

    front = {
        "name": skill_name(pack.slug),
        "description": pack.summary,
        "decibyl": block,
    }
    frontmatter = yaml.safe_dump(
        front, sort_keys=False, allow_unicode=True, width=88
    ).rstrip("\n")

    body = [f"# {pack.name}"]
    for node in template.nodes:
        body.append("")
        body.append(f"## {node.name}")
        body.append(node.prompt)
    return f"---\n{frontmatter}\n---\n" + "\n".join(body) + "\n"


# --------------------------------------------------------------------------
# Loading: folder -> FolderPack
# --------------------------------------------------------------------------


def _sections(body: str, where: str) -> list[tuple[str, str]]:
    """``## name`` sections of the body, in order, each with its text."""
    found: list[tuple[str, list[str]]] = []
    seen_title = False
    for line in body.splitlines():
        heading = _SECTION.match(line)
        if heading:
            found.append((heading.group(1), []))
            continue
        if not found:
            if _TITLE.match(line) and not seen_title:
                seen_title = True
                continue
            if line.strip():
                raise PackFolderError(
                    f"{where} has text before its first '## ' section "
                    f"({line[:40]!r}); every prompt belongs under a node heading"
                )
            continue
        found[-1][1].append(line)
    return [(name, "\n".join(lines).strip("\n")) for name, lines in found]


def _diff(expected: Any, actual: Any, path: str = "") -> Optional[str]:
    """The first place two dumps differ, as a dotted path, or None."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            here = f"{path}.{key}" if path else str(key)
            if key not in expected or key not in actual:
                return here
            found = _diff(expected[key], actual[key], here)
            if found:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path} (length {len(expected)} vs {len(actual)})"
        for index, (left, right) in enumerate(zip(expected, actual)):
            found = _diff(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if expected == actual else path


def _template_from(meta: Any, body: str, where: str) -> AgentTemplate:
    if not isinstance(meta, dict):
        raise PackFolderError(
            f"{where} has no 'template' block under 'decibyl' in its frontmatter"
        )
    declared = meta.get("nodes")
    if not isinstance(declared, list) or not declared:
        raise PackFolderError(f"{where} declares no nodes under 'template'")

    sections = _sections(body, where)
    names = [str(node.get("name", "")) for node in declared if isinstance(node, dict)]
    if [name for name, _ in sections] != names:
        raise PackFolderError(
            f"{where} declares nodes {names} but its body has sections "
            f"{[name for name, _ in sections]}; they must match, in order"
        )

    nodes = []
    for node, (_, prompt) in zip(declared, sections):
        try:
            nodes.append(TemplateNode(**{**node, "prompt": prompt}))
        except (ValidationError, TypeError) as exc:
            raise PackFolderError(
                f"{where} node {node.get('name')!r} is malformed: {exc}"
            ) from exc

    data = {**meta, "nodes": nodes}
    try:
        return AgentTemplate.model_validate(data)
    except ValidationError as exc:
        raise PackFolderError(f"{where} template does not validate: {exc}") from exc


def _pack_from(
    meta: Any,
    skill: PortableSkill,
    template: AgentTemplate,
    *,
    demo_number: Optional[str],
    demo_url: Optional[str],
    where: str,
) -> AgentPack:
    if not isinstance(meta, dict):
        raise PackFolderError(
            f"{where} has no 'pack' block under 'decibyl' in its frontmatter"
        )
    data = dict(meta)
    data["summary"] = skill.description
    data["template_id"] = template.id
    # The catalogue's rule, restated. A demo line belongs to a role that can
    # be rung; a text role has nothing to demonstrate and carries none. And a
    # calling role is on the shelf only when there is a way to hear it, while
    # everything else is on by default.
    calling = bool(
        {
            Channel(c)
            for c in data.get("channels", ())
            if c in Channel._value2member_map_
        }
        & CALLING_CHANNELS
    )
    data["demo_number"] = demo_number if calling else None
    data["demo_url"] = demo_url if calling else None
    if "listed" not in data:
        data["listed"] = bool(demo_number or demo_url) if calling else True
    try:
        pack = AgentPack.model_validate(data)
    except ValidationError as exc:
        raise PackFolderError(f"{where} pack does not validate: {exc}") from exc

    if skill.name != skill_name(pack.slug):
        raise PackFolderError(
            f"{where} is named {skill.name!r} but its pack slug is "
            f"{pack.slug!r}; the name must be the slug with hyphens"
        )
    return pack


def _source_from(meta: Any) -> Source:
    if not meta:
        return Source()
    if not isinstance(meta, dict):
        raise PackFolderError("'source' must be a block of key: value pairs")
    allowed = {f.name for f in fields(Source)}
    return Source(**{k: str(v) for k, v in meta.items() if k in allowed and v})


def _read_dir(path: Path) -> dict[str, str]:
    if not path.is_dir():
        return {}
    return {
        file.name: file.read_text(encoding="utf-8")
        for file in sorted(path.iterdir())
        if file.is_file()
    }


def load(
    folder: str | Path,
    *,
    demo_number: Optional[str] = None,
    demo_url: Optional[str] = None,
) -> FolderPack:
    """Load one pack folder.

    ``demo_number`` and ``demo_url`` are the deployment's, not the folder's:
    the same folder is unlisted on a deployment with no demo line and listed
    on one with, exactly as the catalogue behaves.

    A template the catalogue does not know is registered so the pack
    resolves. One it does know must match, or this raises naming the field.
    """
    path = Path(folder)
    skill_path = path / SKILL_FILE
    where = str(skill_path)
    if not skill_path.is_file():
        raise PackFolderError(f"{path} has no {SKILL_FILE}")

    try:
        skill = parse(skill_path.read_text(encoding="utf-8"), source=where)
    except SkillParseError as exc:
        raise PackFolderError(str(exc)) from exc

    meta = skill.metadata.get("decibyl")
    if not isinstance(meta, dict):
        raise PackFolderError(
            f"{where} is a skill but not a pack: its frontmatter has no 'decibyl' block"
        )
    if meta.get("format") != FORMAT_VERSION:
        raise PackFolderError(
            f"{where} is pack format {meta.get('format')!r}; this build reads "
            f"format {FORMAT_VERSION}"
        )
    draft = bool(meta.get("draft", False))
    source = _source_from(meta.get("source"))

    template = _template_from(meta.get("template"), skill.body, where)
    existing = get_template(template.id)
    if existing is not None:
        drift = _diff(
            existing.model_dump(mode="json", exclude={"suggested_voices"}),
            template.model_dump(mode="json", exclude={"suggested_voices"}),
        )
        if drift:
            raise PackFolderError(
                f"{where} names template {template.id!r} but differs from it "
                f"at {drift}; the catalogue is what runs, so regenerate the "
                "folder or change the catalogue"
            )
        template = existing
    else:
        register_template(template)
        template = get_template(template.id) or template

    pack = _pack_from(
        meta.get("pack"),
        skill,
        template,
        demo_number=demo_number,
        demo_url=demo_url,
        where=where,
    )
    return FolderPack(
        path=path,
        skill=skill,
        pack=pack,
        template=template,
        source=source,
        draft=draft,
        references=_read_dir(path / REFERENCES_DIR),
        scripts=tuple(_read_dir(path / SCRIPTS_DIR)),
    )


def load_all(
    root: str | Path,
    *,
    demo_number: Optional[str] = None,
    demo_url: Optional[str] = None,
) -> tuple[FolderPack, ...]:
    """Every pack folder directly under ``root``, in name order.

    Raises on the first bad folder rather than skipping it: these are our
    own folders under version control, and a folder that stopped loading is
    a test failure, not a warning in a log nobody reads.
    """
    base = Path(root)
    if not base.is_dir():
        return ()
    return tuple(
        load(child, demo_number=demo_number, demo_url=demo_url)
        for child in sorted(base.iterdir())
        if child.is_dir() and (child / SKILL_FILE).is_file()
    )


def live_folders(
    *, demo_number: Optional[str] = None, demo_url: Optional[str] = None
) -> tuple[FolderPack, ...]:
    """The catalogue's packs, as folders. Must equal the catalogue."""
    return load_all(LIVE_DIR, demo_number=demo_number, demo_url=demo_url)


def draft_folders() -> tuple[FolderPack, ...]:
    """Roles being written. Never listed, never on the shelf."""
    return load_all(DRAFTS_DIR)


def check_drift(
    packs: Iterable[tuple[AgentPack, AgentTemplate]], root: str | Path
) -> list[str]:
    """Folders under ``root`` whose text is not what :func:`render` writes.

    For the ``--check`` mode of the export script and for the test that
    keeps ``packs/live`` equal to the catalogue.
    """
    stale: list[str] = []
    for pack, template in packs:
        file = Path(root) / pack.slug / SKILL_FILE
        expected = render(pack, template)
        if not file.is_file() or file.read_text(encoding="utf-8") != expected:
            stale.append(pack.slug)
    return stale


__all__ = [
    "DRAFTS_DIR",
    "FORMAT_VERSION",
    "FolderPack",
    "LIVE_DIR",
    "PACKS_ROOT",
    "PackFolderError",
    "Source",
    "check_drift",
    "draft_folders",
    "live_folders",
    "load",
    "load_all",
    "render",
    "skill_name",
]
