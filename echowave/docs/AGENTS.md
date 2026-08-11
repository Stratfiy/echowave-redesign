# Documentation

## Working relationship

- You can push back on ideas-this can lead to better documentation. Cite sources and explain your reasoning when you do so
- ALWAYS ask for clarification rather than making assumptions
- NEVER lie, guess, or make up information

## Project context

- Format: MDX files with YAML frontmatter
- Config: `docs.json` for navigation. Still the single source of truth: it
  drives the rendered sidebar *and* the MCP docs tools
  (`api/mcp_server/tools/docs_search.py`), which resolve the docs root by
  finding this file. Add a page to `docs.json` or it appears in neither.
- Rendered by: Astro Starlight, to static HTML in `dist/`. The pages were
  authored for Mintlify and the component vocabulary is unchanged — `<Note>`,
  `<Card>`, `<Steps>` and the rest are shimmed in `src/components/mintlify/`
  and injected into every page by `src/remark/inject-components.mjs`, so no
  page needs an import line.
- Files stay where they are. They are deliberately *not* under Starlight's
  conventional `src/content/docs/`, because moving them would break every
  `read_doc` path the MCP tools serve.

## Building

```bash
cd docs
npm install
npm run dev          # local preview
npm run build        # static HTML into docs/dist/
npm run check-links  # fails on any broken internal link
```

`npm run check-links` runs against `dist/`, so build first. It only checks
internal links — external ones fail for reasons that have nothing to do with
this repo.

## Adding a component

If you use a component that is not in `src/components/mintlify/index.ts`, the
build fails with an unresolved reference rather than rendering a blank. Add the
component *and* its name to the list in `src/remark/inject-components.mjs` —
the two lists are the same list.

## Content strategy

- Document just enough for user success - not too much, not too little
- Prioritize accuracy and usability of information
- Make content evergreen when possible
- Search for existing information before adding new content. Avoid duplication unless it is done for a strategic reason
- Check existing patterns for consistency
- Start by making the smallest reasonable changes

## Frontmatter requirements for pages

- title: Clear, descriptive page title
- description: Concise summary for SEO/navigation

## Writing standards

- Second-person voice ("you")
- Prerequisites at start of procedural content
- Test all code examples before publishing
- Match style and formatting of existing pages
- Include both basic and advanced use cases
- Language tags on all code blocks
- Alt text on all images
- Relative paths for internal links

## Git workflow

- NEVER use --no-verify when committing
- Ask how to handle uncommitted changes before starting
- Create a new branch when no clear branch exists for changes
- Commit frequently throughout development
- NEVER skip or disable pre-commit hooks

## Do not

- Skip frontmatter on any MDX file
- Use absolute URLs for internal links
- Include untested code examples
- Make assumptions - always ask for clarification
