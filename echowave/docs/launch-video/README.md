# Decibyl launch video

`decibyl-launch.mp4`: 31 s, 1920×1080, 30 fps, H.264, no audio track.

Six scenes, all copy taken from `LAUNCH-PLAN.md` ("What we are building"), no
new positioning strings:

| Time | Scene |
|---|---|
| 0–3 s | The three agent faces wake up and become the logo |
| 3–8 s | "One assistant for a person's whole life" · home / work / business |
| 8–17 s | Chat: a care task, a preview card, Approve, done ("Always asks before it acts.") |
| 17–22 s | A live phone call and a Hindi WhatsApp reminder ("…in their language.") |
| 22–26 s | Today: a lesson in progress, medicine taken, clinic confirmed ("Teaches them at their own pace.") |
| 26–31 s | End card |

Visuals follow the shipped shell tokens (`ui/src/app/shell-v2.css`) and draw the
blob faces exactly as `ui/src/components/brand/BlobFace.tsx` does.

## Re-render

`launch.html` is one deterministic timeline driven by `seek(t)`; `render.js`
screenshots every frame with Playwright and pipes them to ffmpeg.

```bash
cd echowave/docs/launch-video
NODE_PATH=$(npm root -g) node render.js video             # → decibyl-launch.mp4
NODE_PATH=$(npm root -g) node render.js stills 5 13.5 29  # → still_<t>.png
```

Needs Playwright with Chromium, ffmpeg, and the Inter font installed. The Noto
Indic fonts are vendored in `fonts/` (SIL Open Font License).
