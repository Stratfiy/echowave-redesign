# Skills: where they come from

The Skills shelf ships `.md` files curated from two MIT-licensed
repositories. Each file keeps its `source` and `license` in frontmatter, and
the shelf prints the credit line under the list.

| Source | Licence | What we took |
|---|---|---|
| [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) | MIT | The business divisions: sales, marketing, customer support, finance, product, project management, advertising, strategy, healthcare, research, design. The engineering, security, testing, GIS and game-development divisions are for a different buyer and are not shipped. |
| [affaan-m/ecc](https://github.com/affaan-m/ecc) | MIT | The business-facing skills out of a repository that is otherwise about writing software: brand and content work, deep research, carrier relationships, customs compliance, billing operations, email operations. |

## How a file is normalised on the way in

The parser (`api/services/skills/document.py`) wants a kebab-case `name` and
a `description`. Upstream files carry a display name (`Sales Coach`) or a
slug (`brand-voice`), so the ingest rewrites frontmatter to:

- `name`: the slug, from the filename
- `title`: the display name, prettified when upstream had only a slug
- `description`, `emoji`, `color`, `vibe`: as published
- `division`: which shelf it sits on
- `source`, `license`: the terms, carried with the file

Bodies longer than 500 lines are cut at that line with a note pointing at the
source repository — the limit the parser enforces, and roughly where a
procedure stops being one.

## Adding more

Drop a normalised `.md` into `api/services/skills/catalogue/` and it is on
the shelf at the next release: the loader walks the directory, and
`test_skills_catalogue.py` fails the build if a file does not parse or is
missing its credit.
