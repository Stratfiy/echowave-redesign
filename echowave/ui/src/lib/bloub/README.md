# bloub (vendored)

The agent-face engine, copied from [jeremy-prt/bloub](https://github.com/jeremy-prt/bloub)
at commit `b4bb3c1b5f93c7b87a2e8d620f667c4093d97749`, under the MIT licence in
[`LICENSE`](LICENSE) (© 2026 Jérémy Perret).

Copied as-is from upstream `src/bot/`, with three changes:

- `cycles.ts` (the montage editor) and its test are left out; nothing here plays montages.
- Imports re-sorted for this repo's ESLint (`simple-import-sort`). No other edits.
- Five of the upstream test files come along (`engine`, `shape`, `skins`,
  `expressions`, `face`) so an update that breaks the engine fails here.

Upstream's rule applies: the numbers in these files are measurements, not
settings. Do not round or "tidy" them. To update, copy `src/bot/*.ts` from a
newer upstream commit and re-run `npx eslint --fix src/lib/bloub`.

The React side lives in `src/components/avatar/`. The server stores each agent's
choice of shape, colour and expression (`api/schemas/agent_avatar.py`, whose id
lists must stay in step with `skins.ts` and `expressions.ts`).
