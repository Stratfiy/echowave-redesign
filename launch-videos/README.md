# Decibyl launch videos

- `PLAN.md`: the launch sequence and the video matrix (56 videos across features, adult age groups and job roles).
- `specs/<ID>.json`: one script per video. IDs match the plan.
- `engine/`: turns a script into a finished video. Monochrome product UI, the design's pastel agent faces, docked "Do it / Don't" approvals, and a soundtrack generated from the script's own events.
- `out/<ID>/`: `video.mp4` (poster baked in as frame 0), `poster.jpg`, `share-copy.txt`. A trailing `v` is the 9:16 cut.

```bash
cd launch-videos/engine
NODE_PATH=$(npm root -g) node make.js ../specs/S03.json                   # render
NODE_PATH=$(npm root -g) node make.js ../specs/H1.json --aspect 9:16      # vertical cut -> out/H1v
NODE_PATH=$(npm root -g) node make.js ../specs/S03.json --stills 2,7.3    # check frames first
```

Needs Playwright with Chromium, ffmpeg, Python 3 with numpy, and the Inter font. About 30–40 s per video on 4 cores.

## Scene types

| Type | What it shows | Main fields |
|---|---|---|
| `ask` | The Chat home; the ask types in and sends | `name`, `ask`, `starters` |
| `title` | Kinetic headline with floating faces | `text` ("a\|muted b"), `sub`, `logo` |
| `chat` | A conversation, docked approval, results | `user`, `reply`, `approval {wants, detail}`, `done[]`, `caption` |
| `phones` | One or two phones: `call`, `messages`, `today`, `lock`, `simple`, `settings`, `onboard`, `chat`, `home` | `phones[]`, `caption` or `side`, `chips[]` |
| `step` | Tutorial step: text beside the real screen, a ring and callout on what to touch | `n`, `of`, `title`, `sub`, `focus`, `callout`, `ui {kind, …}` |
| `outro` | Faces, logo, "Join the free early access list", app.decibyl.ai | `line`, `cta` |

Rules for scripts are in `PLAN.md` ("Ground rules"): the documents' own taglines, no new positioning, adults only, and only features verified on staging.
