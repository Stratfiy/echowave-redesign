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
        // Self-hosting and contributor material, kept in the repo and off the
        // public site.
        //
        // Decibyl is a hosted product billed per minute; nobody buying it runs
        // their own Postgres, renews their own certificates or forks anything.
        // These pages told visitors otherwise — a Docker install guide, a
        // Heroku button, "fork maintenance", and an environment-variable
        // reference whose first section explains how to self-host.
        //
        // Excluded here rather than deleted, and removed from `docs.json` in
        // the same change. Removing a page from the nav alone does not
        // unpublish it: pages are generated from this glob, so the URL stays
        // live and Pagefind keeps indexing it. The team still needs these
        // runbooks, so the files stay where they are.
        "!deployment/**",
        "!contribution/**",
        "!developer/environment-variables.mdx",
        // Writing a provider means writing Python in this repository,
        // which is not something a customer of a hosted product can do.
        "!integrations/telephony/custom.mdx",
      ],
    }),
    schema: docsSchema(),
  }),
};
