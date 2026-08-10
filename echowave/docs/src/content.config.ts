import { docsSchema } from "@astrojs/starlight/schema";
import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";

/**
 * Load the docs from where they already live, not from `src/content/docs`.
 *
 * Starlight's convention would have these 129 `.mdx` files under
 * `src/content/docs/`. They stay at the repo's `docs/` root instead, because
 * the MCP docs tools (`api/mcp_server/tools/docs_search.py`) serve pages by
 * paths like `integrations/telephony/plivo` and resolve the docs root by
 * finding `docs.json`. Moving the tree would have broken every `read_doc`
 * call and every link in the agent-building instructions.
 *
 * The exclusions matter: `base: "."` is this directory, which also holds the
 * build output, the node_modules tree and this `src/` folder. Without them the
 * loader would try to render every markdown file in every dependency.
 */
export const collections = {
  docs: defineCollection({
    loader: glob({
      base: ".",
      pattern: [
        "**/*.{md,mdx}",
        "!node_modules/**",
        "!dist/**",
        "!src/**",
        "!.astro/**",
        // Repo-facing notes that are not published pages. They live in docs/
        // for contributors and would otherwise appear in the sidebar as
        // orphans with no nav entry.
        "!AGENTS.md",
        "!CLAUDE.md",
        "!README.md",
        "!DEPLOY-GITHUB-ACTIONS.md",
      ],
    }),
    schema: docsSchema(),
  }),
};
