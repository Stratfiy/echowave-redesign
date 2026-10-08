# /brag plan: Decibyl

**What it is.** One assistant for a person's whole life (home, work and business) that acts on the phone and WhatsApp in their language, teaches them at their own pace, and always asks before it acts.
**Who it's for.** Families and small businesses in India who'd rather say the task than learn an app; the clearest case is looking after an older parent.
**What sets it apart.** It does the real thing (calls the clinic, messages Amma in Hindi), but only after you approve an exact preview.
**Visual hook.** A real request being typed into Decibyl's composer, big, in the first second.
**Get it.** app.decibyl.ai
**Share caption.** See share-copy.txt.

**Angle.** One ordinary evening task ("look after Amma") carried end to end: asked, previewed, approved, done on the phone and WhatsApp, and in Today.
**Tone.** `default`, leaning `polished`, made realistic and current (per review): the real UI inside a browser window at app.decibyl.ai and on 3D-tilted phones, camera push-ins, word-by-word kinetic captions, a soft drifting colour wash with grain, frosted-glass chips, soft staggered transitions.
**Identity.** The product UI keeps `ui/src/app/shell-v2.css` tokens (#0d0d0d ink, #fff paper, #f9f9f9 rail, #ececec lines), Inter / Inter Display, Noto for Indic scripts, the real logo SVG and the three blob shapes from `BlobFace.tsx`. Per review, the faces are drawn in vivid gradients (pink, yellow, green, blue, purple, orange), and each agent keeps its colour throughout.
**Sound.** 100 BPM in F major, F–Dm–B♭–C, one chord per bar (2.4 s), so the outro lands on F exactly at the bar line. A warm pad and sub open the hook, a plucked arpeggio arrives with the reveal, and a soft kick and hat carry the product scenes. Every effect (typing ticks, send pop, approve chime, message pops, chip pops) is pitched in F major pentatonic and goes through the same reverb.

## Storyboard (22 s)

| # | Time | Scene | Readable line (hold ≥ 0.3 s/word) |
|---|---|---|---|
| 1 Hook | 0.0–3.2 | The Chat home in a browser, tilting up as the camera pushes in on the composer: "Remind Amma about her 8 pm medicine, and move Friday's clinic visit to Saturday." types in, Send pressed | the request itself (settled 1.6→2.75) |
| 2 Reveal | 3.2–6.5 | Five floating faces pop in, logo, "One assistant for a person's whole life." | 7 words, settled 4.3→6.3 |
| 3 Highlight | 6.5–12.2 | Chat in the browser, camera leans in on the card: reply streams, preview card ("Call Sunrise Clinic…"), cursor taps Approve, two confirmations | "Always asks before it acts." settled 7.6→12.0 |
| 4 Highlight | 12.3–16.5 | Two phones in 3D: a live call to the clinic and the Hindi WhatsApp thread; glass chips in five scripts | "Acts on the phone and WhatsApp, in their language." settled 13.0→16.3 |
| 5 Highlight | 16.5–19.2 | Today on a phone: lesson 3 of 10 filling, medicine taken, clinic confirmed | "Teaches them at their own pace." settled 17.2→19.1 |
| 6 Outro | 19.2–22.0 | Floating faces, logo, "Try it at app.decibyl.ai" | settled 20.6→22.0 |

All taglines are the launch plan's own words (`echowave/LAUNCH-PLAN.md`). Illustrative UI text only (names, times); no invented numbers or claims.

## Build

`work/launch.html` is one timeline where every frame is a pure function of `seek(t)`; `work/render.js` screenshots each frame with Playwright and pipes it to ffmpeg (`node work/render.js video`, or `stills <t>…`). `work/music.py` synthesises the soundtrack with numpy. The final mux bakes the poster (`brag.jpg`, the 15.4 s frame) in as frame 0 and loudness-normalises the audio to −16 LUFS.
