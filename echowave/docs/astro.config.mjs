// @ts-check
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

import { injectDocComponents } from "./src/remark/inject-components.mjs";

/**
 * `docs.json` stays the single source of truth for navigation.
 *
 * It is read here to build Starlight's sidebar, and it is *also* read at
 * runtime by the MCP docs tools (`api/mcp_server/tools/docs_search.py`), which
 * resolve the docs root by looking for this exact file. Two consumers, one
 * file — so a page added to the nav appears in the site and in
 * `search_docs` without anyone remembering to update a second list.
 *
 * That constraint is why this project does not use Starlight's conventional
 * `src/content/docs/` layout: moving 129 `.mdx` files under it would have
 * broken every `read_doc` path the MCP tools serve.
 */
const docsJson = JSON.parse(
  readFileSync(new URL("./docs.json", import.meta.url), "utf8"),
);

/** Turn one `docs.json` page entry into a Starlight sidebar entry. */
function toSidebarEntry(page) {
  // A nested group: `{ group: "...", pages: [...] }`.
  if (typeof page === "object" && page !== null && Array.isArray(page.pages)) {
    return {
      label: page.group,
      collapsed: true,
      items: page.pages.map(toSidebarEntry).filter(Boolean),
    };
  }

  if (typeof page !== "string") {
    return null;
  }

  // Mintlify writes `getting-started/index` for a section landing page; the
  // rendered URL is the directory itself.
  const slug = page.endsWith("/index") ? page.slice(0, -"/index".length) : page;
  return { slug: slug || "index" };
}

function buildSidebar() {
  const groups = [];
  for (const tab of docsJson.navigation?.tabs ?? []) {
    for (const group of tab.groups ?? []) {
      groups.push({
        // Tab-qualified so two tabs may hold a group of the same name without
        // silently merging in the sidebar.
        label: tab.tab === "Guides" ? group.group : `${tab.tab}: ${group.group}`,
        collapsed: true,
        items: (group.pages ?? []).map(toSidebarEntry).filter(Boolean),
      });
    }
  }
  return groups;
}

export default defineConfig({
  site: process.env.DOCS_SITE_URL || "https://docs.decibyl.ai",
  // Emitted as plain static HTML: nginx serves it from disk with no Node
  // runtime on the box, which is the whole reason this renderer was chosen
  // over a Next.js-based one.
  output: "static",
  // Mintlify served these pages at `/deployment/docker` — no trailing slash —
  // and the tree's relative links were authored against that. Emitting
  // directories instead (`/deployment/docker/`) silently re-bases every
  // relative link one level deeper, which the link checker caught as 11 dead
  // links that had worked for years.
  //
  // Matching the old URL shape fixes them without editing content, and keeps
  // every external link and search result that already points at these pages
  // working. nginx's `try_files $uri $uri.html` serves them extension-less.
  build: { format: "file" },
  trailingSlash: "never",
  outDir: "./dist",
  srcDir: "./src",
  publicDir: "./public",
  // The tree has no page at `/` — Mintlify served `getting-started/index`
  // there. Without this, docs.decibyl.ai returns the 404 page, which is a
  // worse first impression than any missing content.
  redirects: {
    "/": "/getting-started/",
  },
  markdown: {
    remarkPlugins: [injectDocComponents],
  },
  vite: {
    resolve: {
      alias: {
        // The remark plugin injects `from "@components/mintlify"` into every
        // page that uses one. Declared for Vite as well as TypeScript because
        // the injected import is resolved by the bundler, which does not read
        // tsconfig paths.
        "@components": fileURLToPath(new URL("./src/components", import.meta.url)),
      },
    },
  },
  integrations: [
    starlight({
      title: docsJson.name || "Decibyl AI",
      favicon: "/favicon.ico",
      customCss: ["./src/styles/docs.css"],
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/Stratfiy/echowave-redesign",
        },
      ],
      sidebar: buildSidebar(),
      // Starlight's own 404 and edit links are fine; what matters here is that
      // a broken sidebar slug fails the build rather than shipping a dead link.
      pagination: false,
    }),
  ],
});
