"""Turn the compliance drafts into the pages the click-wrap points at.

``services/compliance/agreements.py`` blocks a campaign until the customer has
accepted the DPA, records the version, the IP and the user agent, and never
updates a row. All of that is worth nothing if the URL in the agreement is a
404 -- which it is today: ``landing/`` is empty by design, so
``https://decibyl.ai/legal/dpa`` answers with a pointer to the app.

**A recorded acceptance of a document the customer could not read is worse than
no acceptance at all.** Enforceability under IT Act s10A needs reasonable
notice, an affirmative act, and terms accessible *before* acceptance. The
middle one is built and the other two are not, and the row in the database
makes it look as though all three were.

So this renders the drafts to static HTML under ``landing/``, which nginx
serves with no restart and no config change.

**It refuses to publish a document that still says [TO CONFIRM].**

That marker means a decision nobody has made -- a liability cap, a grievance
officer's name, a retention period. Publishing one would put a placeholder in
front of a customer inside a document they are being asked to agree to, and the
whole point of the marker (see ``compliance/README.md``) is that guessing
produces something confidently wrong that nobody notices until it matters.
Refusing is the loud failure; publishing a half-finished contract is the quiet
one.

Run it::

    python -m scripts.publish_legal            # render, or explain what blocks it
    python -m scripts.publish_legal --check    # report only, write nothing

Exit status is 0 when every required document rendered, 1 otherwise, so it can
gate a deploy the same way the other verifiers do.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "compliance"
OUTPUT = ROOT / "landing"

#: The marker the drafts use for a decision nobody has taken yet.
UNRESOLVED = "TO CONFIRM"


@dataclass(frozen=True)
class Document:
    """One page, and the agreement key it backs.

    ``slug`` is the path under ``landing/``, and it must match the ``url`` on
    the corresponding :class:`~api.services.compliance.agreements.Agreement`.
    A test asserts that rather than trusting this comment.
    """

    slug: str
    source: str
    title: str
    #: The agreement key from agreements.py, or None for a page nobody accepts.
    agreement: str | None


DOCUMENTS: tuple[Document, ...] = (
    Document("legal/dpa", "DPA-TEMPLATE.md", "Data Processing Agreement", "dpa"),
    Document("legal/terms", "TERMS-SKELETON.md", "Terms of Service", "terms"),
    Document("privacy", "PRIVACY-NOTICE-FACTS.md", "Privacy Policy", "privacy"),
    Document("trust", "TRUST.md", "Trust and security", None),
)

#: Sections that exist to brief whoever is finishing the draft, not the
#: customer reading it. Everything from the marker to the next H1 is dropped.
#:
#: A blocklist rather than an allowlist, deliberately: a new section nobody
#: classified should REACH the page, where somebody will see it, rather than
#: vanish from a contract silently. Same rule as api/AGENTS.md.
INTERNAL_UNTIL_NEXT_H1 = (
    "# Data Processing Agreement — template",
    "# Terms of Service — skeleton",
)

STYLE = """
:root { color-scheme: light dark; --ink:#111; --muted:#555; --rule:#e3e3e3; --bg:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#e8e8e8; --muted:#a0a0a0; --rule:#333; --bg:#141414; }
}
body { background:var(--bg); color:var(--ink); margin:0;
  font:16px/1.65 ui-serif, Georgia, "Times New Roman", serif; }
main { max-width:46rem; margin:0 auto; padding:3rem 1.25rem 6rem; }
h1 { font-size:1.9rem; line-height:1.2; margin:0 0 .3rem; }
h2 { font-size:1.2rem; margin:2.4rem 0 .6rem; }
h3 { font-size:1rem; margin:1.6rem 0 .4rem; }
p, li { margin:.7rem 0; }
table { border-collapse:collapse; width:100%; margin:1.2rem 0; font-size:.93rem; }
th, td { border:1px solid var(--rule); padding:.5rem .6rem; text-align:left;
  vertical-align:top; }
th { background:color-mix(in srgb, var(--rule) 40%, transparent); }
code { font:0.9em ui-monospace, SFMono-Regular, Menlo, monospace; }
hr { border:0; border-top:1px solid var(--rule); margin:2.5rem 0; }
.version { color:var(--muted); font-size:.9rem; margin:0 0 2rem;
  font-family:system-ui, sans-serif; }
.wrap { overflow-x:auto; }
a { color:inherit; }
"""


def strip_internal(text: str) -> str:
    """Drop the sections written for whoever finishes the draft."""
    for marker in INTERNAL_UNTIL_NEXT_H1:
        if marker not in text:
            continue
        start = text.index(marker)
        rest = text[start + len(marker) :]
        following = re.search(r"^# ", rest, re.M)
        text = text[:start] + (rest[following.start() :] if following else "")
    return text


def unresolved(text: str) -> list[str]:
    """Every line still carrying the marker, with its number."""
    return [
        f"{number}: {line.strip()[:120]}"
        for number, line in enumerate(text.splitlines(), start=1)
        if UNRESOLVED in line
    ]


def _inline(text: str) -> str:
    """Escape, then re-apply the inline marks. Escaping first, always: these
    documents quote vendor names and paths, and one stray angle bracket would
    otherwise become markup inside a contract."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    out = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', out)
    return out


def to_html(markdown: str) -> str:
    """A deliberately small renderer.

    These documents use headings, paragraphs, lists, tables and rules, and
    nothing else. A dependency would be a larger surface than the six cases
    below, in a script whose output is a contract.
    """
    lines = markdown.splitlines()
    out: list[str] = []
    in_list = False
    in_table = False

    def close() -> None:
        nonlocal in_list, in_table
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_table:
            out.append("</table></div>")
            in_table = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            close()
            continue
        if line.startswith("#"):
            close()
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
        elif set(line.strip()) <= {"-", " "} and line.strip().startswith("---"):
            close()
            out.append("<hr>")
        elif line.lstrip().startswith(("- ", "* ")):
            if in_table:
                close()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line.lstrip()[2:])}</li>")
        elif line.strip().startswith("|"):
            if in_list:
                close()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # The |---|---| separator row carries no content.
            if all(set(c) <= {"-", ":", " "} and c for c in cells):
                continue
            if not in_table:
                out.append('<div class="wrap"><table>')
                in_table = True
                out.append(
                    "<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>"
                )
                continue
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        else:
            close()
            out.append(f"<p>{_inline(line)}</p>")
    close()
    return "\n".join(out)


def page(document: Document, body: str, version: str | None) -> str:
    stamp = f"<p class='version'>Version {html.escape(version)}</p>" if version else ""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(document.title)} — Decibyl</title>"
        f"<style>{STYLE}</style></head><body><main>"
        f"{stamp}{body}"
        "</main></body></html>\n"
    )


def versions() -> dict[str, str]:
    """The published version of each agreement, read from the one place that
    decides it. Copying it here would let a page claim a version the acceptance
    record disagrees with."""
    sys.path.insert(0, str(ROOT))
    from api.services.compliance.agreements import CURRENT_VERSIONS

    return dict(CURRENT_VERSIONS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only")
    args = parser.parse_args()

    published = versions()
    blocked: list[tuple[Document, list[str]]] = []
    ready: list[Document] = []

    for document in DOCUMENTS:
        source = SOURCE / document.source
        if not source.exists():
            print(f"[ FAIL ] {document.slug}: {source} is missing")
            blocked.append((document, ["source file missing"]))
            continue

        text = strip_internal(source.read_text())
        problems = unresolved(text)
        if problems:
            blocked.append((document, problems))
            continue

        ready.append(document)
        if args.check:
            continue

        target = OUTPUT / document.slug / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        version = published.get(document.agreement) if document.agreement else None
        target.write_text(page(document, to_html(text), version))
        print(f"[  ok  ] {document.slug} -> {target.relative_to(ROOT)}")

    for document, problems in blocked:
        print(f"\n[ FAIL ] {document.slug} ({document.source}) is not publishable.")
        print(f"         {len(problems)} decision(s) nobody has taken:")
        for problem in problems[:12]:
            print(f"           line {problem}")
        if len(problems) > 12:
            print(f"           ... and {len(problems) - 12} more")

    if blocked:
        print(
            "\nNothing was written for the documents above. A placeholder inside a "
            "\ncontract somebody is being asked to accept is worse than a missing page:"
            "\nthe missing page is noticed, and the placeholder is agreed to."
        )
        return 1

    print(f"\n{len(ready)} document(s) published under {OUTPUT.relative_to(ROOT)}/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
