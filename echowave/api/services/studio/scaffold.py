"""The starting point of every Studio site: a designed React app built by Vite.

The tree lives on disk under ``starter/<framework>/`` so it is maintained as
code, not as strings. It is not a blank page: Tailwind CSS v4 with design
tokens, typefaces bundled from npm, lucide icons, motion, and a set of
finished sections (navbar, hero, features, steps, stats, testimonials,
pricing, FAQ, call to action, contact form, footer) that render the content
in ``src/site.js``. The model starts from something that already looks
professional and spends its effort on the business's words, pictures and
the sections it adds -- the floor is the design, not a blank ``<h1>``.

Three choices here are load-bearing rather than taste:

- ``base: './'`` in the Vite config. The preview serves a site from a path
  (``/api/v1/public/sites/<token>/``), so asset URLs must be relative or the
  page loads with no script and no styles.
- The agents block in ``index.html``. :func:`set_agents_block` rewrites the
  text between the two markers and nothing else, so the model can redesign
  the page freely without being trusted to paste an embed script correctly.
- ``src/lib/config.js``. :func:`set_form_endpoint` writes the one line the
  contact form posts to, for the same reason.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

from api.services.studio import themes

FRAMEWORK_VITE_REACT = "vite-react"
FRAMEWORKS = (FRAMEWORK_VITE_REACT,)

#: Where ``npm run build`` writes, for each framework.
OUTPUT_DIR = {FRAMEWORK_VITE_REACT: "dist"}

AGENTS_START = "<!-- decibyl-agents:start -->"
AGENTS_END = "<!-- decibyl-agents:end -->"
CONFIG_PATH = "src/lib/config.js"

_STARTER_ROOT = Path(__file__).resolve().parent / "starter"


@cache
def _starter(framework: str) -> dict[str, str]:
    root = _STARTER_ROOT / framework
    if not root.is_dir():
        raise ValueError(
            f"Unknown framework {framework!r}; choose one of {', '.join(FRAMEWORKS)}."
        )
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def starter_files(
    framework: str, *, title: str, theme: str = themes.DEFAULT_THEME
) -> dict[str, str]:
    """The tree a new site starts from, named and themed."""
    if framework not in FRAMEWORKS:
        raise ValueError(
            f"Unknown framework {framework!r}; choose one of {', '.join(FRAMEWORKS)}."
        )
    chosen = themes.get(theme)
    name = (title or "My site").strip()[:120] or "My site"
    initial = _html(name[:1].upper())
    files: dict[str, str] = {}
    for path, text in _starter(framework).items():
        text = (
            text.replace("{{title}}", _html(name))
            .replace("{{description}}", _html(f"{name} — talk to us any time."))
            .replace("{{theme_color}}", chosen.colors["brand"])
            .replace("{{initial}}", initial)
            # json.dumps gives a valid JS string literal for any name.
            .replace("__NAME__", json.dumps(name, ensure_ascii=False))
        )
        files[path] = text
    files[".gitignore"] = "node_modules\ndist\n"
    return themes.apply(files, chosen)


_BLOCK = re.compile(re.escape(AGENTS_START) + r".*?" + re.escape(AGENTS_END), re.DOTALL)


def set_agents_block(index_html: str, snippets: list[str]) -> str:
    """``index_html`` with the agents block holding exactly ``snippets``.

    If the model rewrote the page and dropped the markers, the block goes back
    in before ``</body>`` -- an agent that silently fails to appear on the site
    is the failure this function exists to prevent.
    """
    block = AGENTS_START + "\n" + "\n".join(snippets) + "\n" + AGENTS_END
    if _BLOCK.search(index_html):
        return _BLOCK.sub(lambda _: block, index_html, count=1)
    lowered = index_html.lower()
    at = lowered.rfind("</body>")
    if at == -1:
        return index_html.rstrip("\n") + "\n" + block + "\n"
    return index_html[:at] + block + "\n" + index_html[at:]


def config_js(form_endpoint: str) -> str:
    return (
        "// Written by Studio. FORM_ENDPOINT is where the contact form posts.\n"
        f"export const FORM_ENDPOINT = {json.dumps(form_endpoint)};\n"
    )
