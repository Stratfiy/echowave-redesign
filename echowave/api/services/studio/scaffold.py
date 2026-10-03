"""The starting point of every Studio site: a React app built by Vite.

One framework, chosen because it builds in seconds inside the sandbox, needs
no server at runtime, and its output is a folder of static files that can be
previewed here and hosted anywhere. The model starts from this tree and edits
it; it never has to remember how to set up a bundler.

Two choices here are load-bearing rather than taste:

- ``base: './'`` in the Vite config. The preview serves a site from a path
  (``/api/v1/public/sites/<token>/``), not from the root of a domain, so asset
  URLs must be relative or the page loads with no script and no styles.
- The agents block in ``index.html``. :func:`set_agents_block` rewrites the
  text between the two markers and nothing else, so the model can redesign
  the page freely without being trusted to paste an embed script correctly.
"""

from __future__ import annotations

import json
import re

FRAMEWORK_VITE_REACT = "vite-react"
FRAMEWORKS = (FRAMEWORK_VITE_REACT,)

#: Where ``npm run build`` writes, for each framework.
OUTPUT_DIR = {FRAMEWORK_VITE_REACT: "dist"}

AGENTS_START = "<!-- decibyl-agents:start -->"
AGENTS_END = "<!-- decibyl-agents:end -->"

_PACKAGE_JSON = {
    "name": "decibyl-site",
    "private": True,
    "version": "0.1.0",
    "type": "module",
    "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
    "dependencies": {"react": "^19.3.0", "react-dom": "^19.3.0"},
    "devDependencies": {"@vitejs/plugin-react": "^6.1.1", "vite": "^8.3.2"},
}

_INDEX_HTML = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{title}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
    {AGENTS_START}
    {AGENTS_END}
  </body>
</html>
"""

_VITE_CONFIG = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "./" keeps every asset URL relative, so the built site works from
// any path -- the Studio preview, a sub-folder, or the root of a domain.
export default defineConfig({
  plugins: [react()],
  base: "./",
});
"""

_MAIN_JSX = """import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
"""

_APP_JSX = """export default function App() {
  return (
    <main className="page">
      <h1>{title}</h1>
      <p>Your site is ready. Ask in the Studio chat to design it.</p>
    </main>
  );
}
"""

_INDEX_CSS = """:root {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color: #111827;
  background: #ffffff;
}

body {
  margin: 0;
}

.page {
  max-width: 960px;
  margin: 0 auto;
  padding: 64px 16px;
}
"""

_GITIGNORE = "node_modules\ndist\n"


def starter_files(framework: str, *, title: str) -> dict[str, str]:
    """The tree a new site starts from."""
    if framework != FRAMEWORK_VITE_REACT:
        raise ValueError(
            f"Unknown framework {framework!r}; choose one of {', '.join(FRAMEWORKS)}."
        )
    safe_title = (title or "My site").strip()[:120]
    html_title = (
        safe_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return {
        "package.json": json.dumps(_PACKAGE_JSON, indent=2) + "\n",
        "index.html": _INDEX_HTML.replace("{title}", html_title),
        "vite.config.js": _VITE_CONFIG,
        "src/main.jsx": _MAIN_JSX,
        # json.dumps gives a valid JS string literal for any title.
        "src/App.jsx": _APP_JSX.replace("{title}", "{" + json.dumps(safe_title) + "}"),
        "src/index.css": _INDEX_CSS,
        ".gitignore": _GITIGNORE,
    }


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
