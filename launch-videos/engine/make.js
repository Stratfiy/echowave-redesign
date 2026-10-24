// Build one video from a spec: frames (parallel), soundtrack, mux, poster, share copy.
//   node make.js ../specs/S03.json            -> ../out/S03/{video.mp4,poster.jpg,share-copy.txt}
//   node make.js ../specs/S03.json --stills 1.2,4,9.5   -> ../out/S03/stills/
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const specPath = path.resolve(process.argv[2]);
const spec = JSON.parse(fs.readFileSync(specPath, 'utf8'));
const aspectArg = process.argv.indexOf('--aspect');
if (aspectArg > 0 && process.argv[aspectArg + 1] !== spec.aspect) { spec.aspect = process.argv[aspectArg + 1]; spec.id += 'v'; }
const stillsArg = process.argv.indexOf('--stills');
const outDir = path.resolve(__dirname, '..', 'out', spec.id);
const work = path.join(outDir, 'work');
fs.mkdirSync(work, { recursive: true });
const FPS = 30, WORKERS = +(process.env.WORKERS || 4);

async function openPage(browser, size) {
  const page = await browser.newPage({ viewport: { width: size[0], height: size[1] }, deviceScaleFactor: 1 });
  await page.addInitScript((s) => { window.SPEC = s; }, spec);
  await page.goto('file://' + path.join(__dirname, 'player.html'));
  await page.evaluate(async () => { await document.fonts.ready; window.seek(window.DURATION / 2); await document.fonts.ready; window.seek(0); });
  await page.waitForTimeout(200);
  return page;
}

(async () => {
  const size = spec.aspect === '9:16' ? [1080, 1920] : [1920, 1080];
  const browser = await chromium.launch();
  const probe = await openPage(browser, size);
  const meta = await probe.evaluate(() => ({ duration: window.DURATION, events: window.EVENTS, bounds: window.BOUNDS }));
  fs.writeFileSync(path.join(work, 'meta.json'), JSON.stringify({ ...meta, music: spec.music || 'story' }, null, 1));
  console.log(`${spec.id}: ${meta.duration.toFixed(1)} s, ${meta.events.length} sound events`);

  if (stillsArg > 0) {
    const dir = path.join(outDir, 'stills'); fs.mkdirSync(dir, { recursive: true });
    for (const t of process.argv[stillsArg + 1].split(',')) {
      await probe.evaluate((t) => window.seek(t), +t);
      await probe.screenshot({ path: path.join(dir, `s_${t}.png`) });
    }
    await browser.close(); return;
  }
  await probe.close();

  const n = Math.round(meta.duration * FPS), per = Math.ceil(n / WORKERS);
  const t0 = Date.now();
  await Promise.all(Array.from({ length: WORKERS }, async (_, w) => {
    const a = w * per, b = Math.min(n, a + per);
    if (a >= b) return;
    const page = await openPage(browser, size);
    const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-pix_fmt', 'yuv420p', path.join(work, `seg${w}.mp4`)], { stdio: ['pipe', 'ignore', 'inherit'] });
    for (let i = a; i < b; i++) {
      await page.evaluate((t) => window.seek(t), i / FPS);
      const buf = await page.screenshot({ type: 'jpeg', quality: 94 });
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    }
    ff.stdin.end();
    await new Promise((r) => ff.on('close', r));
    await page.close();
  }));
  await browser.close();
  console.log(`frames: ${n} in ${((Date.now() - t0) / 1000).toFixed(0)} s`);

  const list = Array.from({ length: WORKERS }, (_, w) => `file 'seg${w}.mp4'`).filter((_, w) => fs.existsSync(path.join(work, `seg${w}.mp4`))).join('\n');
  fs.writeFileSync(path.join(work, 'segs.txt'), list);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', path.join(work, 'segs.txt'), '-c', 'copy', path.join(work, 'silent.mp4')]);
  execFileSync('python3', [path.join(__dirname, 'music.py'), path.join(work, 'meta.json'), path.join(work, 'soundtrack.wav')], { stdio: 'inherit' });

  // Poster: the spec's settled frame, baked in as frame 0.
  const poster = path.join(outDir, 'poster.jpg');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', String(spec.poster ?? meta.duration * 0.6), '-i', path.join(work, 'silent.mp4'), '-frames:v', '1', '-q:v', '2', poster]);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', path.join(work, 'silent.mp4'), '-loop', '1', '-i', poster, '-i', path.join(work, 'soundtrack.wav'),
    '-filter_complex', "[0:v][1:v]overlay=enable='eq(n,0)':shortest=1,format=yuv420p[v];[2:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
    '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
    '-movflags', '+faststart', '-t', String(meta.duration), path.join(outDir, 'video.mp4')]);
  fs.writeFileSync(path.join(outDir, 'share-copy.txt'), (spec.share || '') + '\n');
  console.log(`done: ${path.join(outDir, 'video.mp4')}`);
})();
