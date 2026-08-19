# Image audit

Companion to `SHOTLIST.md`, which lists the screenshots still to capture. This
one records what was **taken down** and why.

## What was wrong

The docs tree came from Dograh, the upstream project. The `.mdx` pages were
rewritten at the rebrand; the images were not. Fifteen images live on
docs.decibyl.ai showed Dograh's dashboard, Dograh's marketing homepage, or a
Dograh URL — a reader following `voice-agent/add-to-website` went through
another company's product from top to bottom.

Four of them additionally documented a model-configuration screen whose tabs
were *Speech to Speech / Dograh / BYOK*, a layout the product no longer has.

## What changed

**All fifteen references are gone**, and so are the files. Each one is now a
`<Screenshot>` slot carrying a capture brief, so the pages read correctly today
and the image appears the moment somebody drops a replacement into
`docs/images/` under the named filename. Nothing off-brand is published while
that happens; a slot with no file renders as nothing.

| Removed | What was visible | Now a slot on |
|---|---|---|
| `floating-widget-example.png` | Dograh's marketing homepage — "all Open Source", "Star us on GitHub", an "Ask Dograh" widget | *replaced outright, see below* |
| `inline-widget-example.png` | Same homepage, plus a card headed "Dograh Assistant" | *replaced outright* |
| `headless-widget-example.png` | Same homepage, sample questions naming Dograh | *replaced outright* |
| `open-settings.png` | Sidebar reading "Dograh v1.27.0", GitHub star button, an editor header the product no longer has | `voice-agent/add-to-website` |
| `add-to-website.png` | Dograh sidebar, "Join Slack", GitHub star button | `voice-agent/add-to-website` |
| `save-configurations.png` | Widget preview with button text "Ask Dograh", twice | `voice-agent/add-to-website` |
| `copy-deployment-code.png` | Dograh sidebar; an embed snippet pointing at `localhost:3010` with a live-looking token and an ngrok URL | `voice-agent/add-to-website` |
| `api-keys.png` | "Manage your API keys to access Dograh services"; keys on the retired `dgr_` prefix | `configurations/api-keys` |
| `service-keys.png` | "Dograh Service Keys"; a key named "Dograh Production Service Keys" | `configurations/api-keys` |
| `accessing-traces.png` | A trace URL reading `https://langfuse.dograh.com/…` | `configurations/tracing` |
| `models_dropdown.png` | Provider dropdown set to `dograh` | `configurations/llm` |
| `add_model_manually.png` | Provider dropdown set to `dograh` | `configurations/llm` |
| `model-configuration-decibyl.png` | Filename said decibyl; the tab in the image said **Dograh** | `configurations/inference-providers` |
| `model-configuration-byok.png` | Same tab strip, same "Dograh" tab | `configurations/inference-providers` |
| `model-configuration-speech-to-speech.png` | Same retired tab strip | `configurations/inference-providers` |
| `template-variables.png` | "Join Slack" button in the header | `voice-agent/template-variables` |

### Replaced outright, not slotted

The three embed-mode images were never screenshots of *our* product — they were
screenshots of Dograh's marketing site with Dograh's widget on it. What the
page actually needs there is a picture of **where each mode puts the widget on
your own page**, which is a diagram, not a capture.

So they are now authored SVGs, drawn against a deliberately generic wireframe
page: `embed-mode-floating.svg`, `embed-mode-inline.svg`,
`embed-mode-headless.svg`. They carry their own light and dark palettes, scale
without going soft, and weigh about 3 KB each. Nothing to recapture.

### Also deleted, unreferenced

`tool attachment.png`, `tool description.png`, `tool params.png` (Dograh's tool
screens, on no page — `voice-agent/tools/http-api` still has no images and
wants replacements), plus `checks-passed.png`, `go-to-deployment.png`,
`star-history.png`, `video_thumbnail_1.png`, `hero.gif`, `hero-light.png`,
`hero-dark.png`, `view_trace.png`, `vobiz-inbound-config-1.png`,
`vobiz-inbound-config-2.png` — artefacts of the open-source README this tree no
longer publishes.

`node.png` went too. It was Decibyl-clean and the only Start Call panel shot in
the tree, but it shows a **Detect Voicemail** toggle that no longer exists on
the node — voicemail detection moved to a workflow-level dialog.
`voice-agent/start-call` declares `start-call-panel.png` for the replacement.

## Two of these need prose changes, not just a new file

`configurations/inference-providers` and `configurations/llm` describe the
model-configuration screen as it was before the redesign. Recapturing the
images will not be enough on those two — the instructions around them describe
tabs that are gone.

## Left alone

`create-a-voice-agent.png`, `edge.png`, `global-node.png`,
`extracted_variables.png`, `add_tts_manually.png`, `conversation-history.png`,
`llm-response.png`, and the vendor-console captures (`twilio-*`, `cloudonix-*`,
`vobiz-inbound-config-3`), which are screenshots of those vendors' own products
and correctly show their branding.

`create-a-voice-agent.png` is accurate and current but shows an **empty** form.
A version with all three fields filled in would teach the page's main point
without the reader having to read the prose to get it.
