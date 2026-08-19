# Images that need replacing

Not a list of missing screenshots — see `SHOTLIST.md` for those. These are
images **already live on docs.decibyl.ai** that should come down.

Decibyl's docs tree was inherited from Dograh, the upstream project, and the
`.mdx` pages were rewritten at the rebrand. The images were not. Fifteen of
them still show Dograh's product, Dograh's marketing site, or a Dograh URL, and
they are being served to every visitor today.

Each one is a screenshot of somebody else's product presented as ours, so the
priority here is not tidiness. Replacing them needs nothing but a session in
the current app and a screenshot tool.

## Off-brand — replace before anything else

**Seven of the fifteen are on one page.** `voice-agent/add-to-website` is
illustrated end to end with Dograh's dashboard and Dograh's marketing site; a
reader following it sees another company's product in every image. Start there.

| Image | What is visible | Appears on |
|---|---|---|
| `floating-widget-example.png` | **Dograh's marketing homepage** — "Powerful Features for Building AI Voice Agents", "all Open Source", "Star us on GitHub", "Try Cloud", an "Ask Dograh" widget | `voice-agent/add-to-website` |
| `inline-widget-example.png` | Same homepage, plus a card headed "Dograh Assistant" and body text about "Dograh's AI voice technology" | `voice-agent/add-to-website` |
| `headless-widget-example.png` | Same homepage, plus sample questions reading "How does Dograh handle objections in real-time?" | `voice-agent/add-to-website` |
| `copy-deployment-code.png` | Dograh sidebar, "Join Slack", GitHub star button, an embed snippet pointing at `localhost:3010` with a live-looking token and an ngrok URL | `voice-agent/add-to-website` |
| `open-settings.png` | Sidebar reading "Dograh v1.27.0", GitHub star button, and an editor header that no longer matches the product | `voice-agent/add-to-website` |
| `add-to-website.png` | Dograh sidebar, "Join Slack", GitHub star button | `voice-agent/add-to-website` |
| `save-configurations.png` | Widget preview with the button text "Ask Dograh", twice | `voice-agent/add-to-website` |
| `service-keys.png` | Heading "Dograh Service Keys", subtitle "Manage service keys for accessing Dograh AI services", a key named "Dograh Production Service Keys" | `configurations/api-keys` |
| `api-keys.png` | "Manage your API keys to access Dograh services", keys shown with the retired `dgr_` prefix | `configurations/api-keys` |
| `accessing-traces.png` | A trace URL reading `https://langfuse.dograh.com/…` | `configurations/tracing` |
| `models_dropdown.png` | Provider dropdown set to `dograh` | `configurations/llm` |
| `add_model_manually.png` | Provider dropdown set to `dograh` | `configurations/llm` |
| `model-configuration-decibyl.png` | Filename says decibyl; the tab in the image says **Dograh** | `configurations/inference-providers` |
| `model-configuration-byok.png` | Same tab strip, same "Dograh" tab | `configurations/inference-providers` |
| `template-variables.png` | "Join Slack" button in the header | `voice-agent/template-variables` |

## Also stale, independent of branding

The model configuration screen was redesigned. Its three tabs used to be
*Speech to Speech* / *Dograh* / *BYOK*; that is what these four images show and
it is not what the screen looks like now, so recapturing them is a rewrite of
those pages' instructions rather than a swap of the files:

- `model-configuration-decibyl.png`
- `model-configuration-byok.png`
- `model-configuration-speech-to-speech.png`
- `models_dropdown.png`

Separately, `node.png` — a Start Call settings panel, and one of the few clean
images in here — shows a **Detect Voicemail** toggle that no longer exists on
the node; voicemail detection moved to a workflow-level dialog. It is left
unreferenced for that reason. `voice-agent/start-call` declares a
`start-call-panel.png` slot for the replacement.

## Unreferenced and safe to delete

In the tree, on nobody's page, and all showing Dograh's product:

- `tool attachment.png`, `tool description.png`, `tool params.png` — the tool
  configuration screens, with Dograh's sidebar and a red annotation arrow.
  `voice-agent/tools/http-api` has no images and wants replacements for these.
- `checks-passed.png`, `go-to-deployment.png`, `star-history.png`,
  `video_thumbnail_1.png`, `hero.gif`, `hero-light.png`, `hero-dark.png` —
  artefacts of the open-source README that this tree no longer publishes.
- `view_trace.png`, `vobiz-inbound-config-1.png`, `vobiz-inbound-config-2.png`

## Clean, keep as they are

`create-a-voice-agent.png`, `edge.png`, `global-node.png`,
`extracted_variables.png`, `add_tts_manually.png`, `conversation-history.png`,
`llm-response.png`, and the vendor-console captures (`twilio-*`,
`cloudonix-*`, `vobiz-inbound-config-3`), which are screenshots of those
vendors' own products and correctly show their branding.

## Worth improving, not urgent

`create-a-voice-agent.png` is accurate and current but shows an **empty** form.
A version with all three fields filled in — a real Activity Description in the
textarea — would teach the page's main point without the reader having to read
the prose to get it.
