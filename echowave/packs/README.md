# Packs as folders

A pack is what somebody hires: a role, what it needs to know, what it needs
connected, and the prompts it runs on. Until now a pack was Python in
`api/services/packs/catalogue.py`. It can now also be a folder, in the
published Agent Skills layout, and the loader in
`api/services/packs/folder.py` turns a folder into the same `AgentPack` and
`AgentTemplate` the catalogue produces.

```
packs/
  live/<slug>/SKILL.md        the eight catalogue packs, regenerated from code
  drafts/<slug>/SKILL.md      roles being written; never listed
  drafts/<slug>/references/   supporting text, read on demand (tests.md)
```

## The folder

```
<slug>/
  SKILL.md       frontmatter: name, description, decibyl block
                 body: one "## <node name>" section per node, holding its prompt
  references/    supporting text a reviewer or a bot may read on demand
  scripts/       listed, never run by the loader
```

The frontmatter's `decibyl` block carries what the skills format has no home
for:

| key | what it holds |
|---|---|
| `format` | `1`; bumped when the block changes shape |
| `draft` | `true` for a role not yet promoted; a draft is never listed |
| `source` | `repo`, `ref` (a tag or commit, never a branch), `path`, `licence` |
| `pack` | the `AgentPack` fields except the summary (the description), the demo line and the listing, which are the deployment's |
| `template` | the `AgentTemplate` fields except the prompts; `nodes` lists each node's name, type, greeting and extract hints |

The body holds the prompts, one `## <node name>` section per declared node, in
order, because a person reviewing a pack should read the thing that runs.

## Two rules

**A folder that names a catalogue template must match it.** The catalogue is
what runs. The loader compares the folder's template to the catalogue's and
refuses drift, naming the field. `packs/live` is therefore generated, not
edited:

```
python -m scripts.export_pack_folders           # regenerate
python -m scripts.export_pack_folders --check   # what the test runs
```

**A template not in the catalogue is registered by id and never listed.** The
pack resolves and can be hired by whoever loaded it; it does not appear in the
template gallery until somebody promotes it into the catalogue.

## Drafts

`packs/drafts` was imported from the prompt pack in
[Stratfiy/decibyl](https://github.com/Stratfiy/decibyl) at
`docs/product/prompts`, pinned to the commit in each folder's `source.ref`,
joined with the shelf (`data/shelf.ts`) for channels and tools:

```
python -m scripts.import_prompt_pack <prompts_dir> <shelf.ts> --ref <commit>
```

Each draft is one node carrying the whole prompt, plus a close; its
`{{variables}}` become the pack's required facts; its `## Tests` section moves
to `references/tests.md` so the prompt does not carry its own test cases.
Promoting a draft means reading it against those tests, splitting it into a
flow where the role needs one, and moving it into the catalogue.

## Checks

`api/tests/test_pack_folders.py` proves that a pack installed from a folder is
the worker the catalogue installs (same pack, same template, byte-identical
workflow definition), that every draft loads and materialises, and that the
data-not-instructions rule reaches every node of every folder-loaded pack.
