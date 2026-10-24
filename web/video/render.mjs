// Renders web/video/index.html to an MP4 frame by frame (deterministic, no screen recording).
//
//   node web/video/render.mjs --serve                      live preview at http://localhost:5199/video/
//   node web/video/render.mjs --stills 3,12,40 [--out dir] PNG stills for review
//   node web/video/render.mjs [--out file.mp4]             full 60 s video with sound
//
// Rendering needs puppeteer-core and ffmpeg-static; install them outside the repo and point RENDER_DEPS at
// that node_modules folder:  npm i --prefix <dir> puppeteer-core@23.11.1 ffmpeg-static@5.2.0
// Voice lines use Windows text-to-speech (System.Speech); elsewhere the video renders without them.
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const FPS = 30, DURATION = 60, W = 1920, H = 1080, RATE = 44100;
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const opt = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);

// ---- static server -----------------------------------------------------------------------------------------

const ROUTES = [
  ['/video/', path.join(HERE)],
  ['/three/', path.join(ROOT, 'web', 'node_modules', 'three', 'build')],
  ['/data/', path.join(ROOT, 'data', 'MUCL')],
];
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.css': 'text/css' };
function serve(port) {
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(req.url.split('?')[0]);
    const route = ROUTES.find(([prefix]) => url.startsWith(prefix));
    const file = route && path.join(route[1], url.slice(route[0].length) || 'index.html');
    if (!file || !file.startsWith(route[1]) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(port, () => resolve(server)));
}

if (flag('--serve')) {
  await serve(5199);
  console.log('http://localhost:5199/video/  (add ?t=12.5 for a still)');
} else {
  await render();
}

// ---- audio -------------------------------------------------------------------------------------------------

function tts(lines, dir) {
  if (process.platform !== 'win32') return {};
  const script = lines
    .map(([key, text]) => {
      const file = path.join(dir, `${key}.wav`).replace(/'/g, "''");
      return `$s.SetOutputToWaveFile('${file}', $fmt); $s.Speak('${text.replace(/'/g, "''")}');`;
    })
    .join('\n');
  const ps = `Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Zira Desktop'); $s.Rate = 1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(${RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
${script}
$s.Dispose()`;
  const r = spawnSync('powershell', ['-NoProfile', '-Command', ps], { encoding: 'utf8' });
  if (r.status !== 0) console.warn('TTS failed, rendering without voice:', r.stderr);
  const out = {};
  for (const [key] of lines) {
    const file = path.join(dir, `${key}.wav`);
    if (!fs.existsSync(file)) continue;
    const buf = fs.readFileSync(file);
    let i = 12;
    while (i < buf.length && buf.toString('ascii', i, i + 4) !== 'data') i += 8 + buf.readUInt32LE(i + 4);
    const n = buf.readUInt32LE(i + 4) / 2;
    out[key] = Float32Array.from({ length: n }, (_, k) => buf.readInt16LE(i + 8 + k * 2) / 32768);
  }
  return out;
}

function soundtrack(voice) {
  const N = DURATION * RATE;
  const mix = new Float32Array(N);
  const add = (t0, dur, fn) => {
    const a = Math.floor(t0 * RATE), n = Math.floor(dur * RATE);
    for (let i = 0; i < n && a + i < N; i++) if (a + i >= 0) mix[a + i] += fn(i / RATE, i / n);
  };
  let seed = 7;
  const noise = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
  const tone = (f, t) => Math.sin(2 * Math.PI * f * t);

  // voice segments, so the music can duck under them
  const VOICE_AT = { answer: 20.8, booked: 50.75, outro: 56.6 };
  const speaking = (t) => Object.entries(VOICE_AT).some(([k, at]) => voice[k] && t >= at - 0.2 && t < at + voice[k].length / RATE + 0.2);

  // music bed: Am – F – C – G, 4 s per chord, soft sines with slow swells
  const CHORDS = [[220, 261.63, 329.63], [174.61, 220, 261.63], [196, 261.63, 329.63], [196, 246.94, 293.66]];
  for (let i = 0; i < N; i++) {
    const t = i / RATE;
    const c = CHORDS[Math.floor(t / 4) % 4];
    const inBar = (t % 4) / 4;
    const env = Math.min(1, inBar * 6) * (1 - 0.35 * inBar);
    const pulse = 0.75 + 0.25 * Math.cos(2 * Math.PI * 2 * t); // 120 bpm breathing
    let v = 0;
    for (const f of c) v += tone(f, t) + 0.25 * tone(f * 2, t);
    v += 0.6 * tone(c[0] / 2, t);
    const master = Math.min(1, t / 1.5) * (1 - clamp01((t - 58.5) / 1.5));
    mix[i] += v * env * pulse * 0.022 * master * (speaking(t) ? 0.45 : 1);
  }

  const whoosh = (t0, dur = 0.7, vol = 0.35) => {
    let lp = 0;
    add(t0, dur, (t, k) => {
      const cut = 0.02 + 0.25 * Math.sin(Math.PI * k);
      lp += cut * (noise() - lp);
      return lp * Math.sin(Math.PI * k) ** 2 * vol;
    });
  };
  const blip = (t0, f, dur = 0.12, vol = 0.18) => add(t0, dur, (t, k) => tone(f, t) * (1 - k) ** 3 * vol);
  const pop = (t0) => add(t0, 0.14, (t, k) => tone(500 + 600 * k, t) * (1 - k) ** 2 * 0.22);
  const chime = (t0, notes = [1046.5, 1318.5, 1568], vol = 0.13) =>
    notes.forEach((f, i) => add(t0 + i * 0.09, 0.9, (t, k) => (tone(f, t) + 0.3 * tone(f * 2, t)) * Math.exp(-5 * k) * vol));
  const typing = (t0, t1, vol = 0.05) => {
    for (let t = t0; t < t1; t += 0.065 + 0.03 * Math.abs(noise())) add(t, 0.012, (_, k) => noise() * (1 - k) * vol);
  };

  whoosh(0.2, 1.6, 0.18);
  whoosh(2.0, 1.6, 0.25);
  whoosh(5.0, 1.2, 0.3);
  chime(7.3, [659.25, 523.25], 0.12); // door ding-dong
  pop(9.0);
  whoosh(12.6, 1.0, 0.3);
  blip(13.6, 880); blip(13.72, 1320);
  [16.5, 18.1, 19.7].forEach((t) => blip(t, 1760, 0.06, 0.12));
  chime(20.6, [1318.5, 1568], 0.08);
  whoosh(25.0, 0.8, 0.3);
  chime(29.0);
  whoosh(29.5, 0.8, 0.25);
  chime(30.9, [1318.5, 1760], 0.16); // mail bing
  whoosh(32.2, 1.0, 0.28);
  typing(36.4, 37.5, 0.07);
  blip(38.0, 1200, 0.05, 0.15);
  whoosh(38.05, 0.6, 0.3);
  typing(38.9, 40.6, 0.035);
  [41.2, 42.2, 43.2, 44.2, 45.2].forEach((t) => blip(t, 1760, 0.06, 0.12));
  [46.2, 46.45, 46.7].forEach((t) => pop(t));
  blip(49.4, 1200, 0.05, 0.15);
  chime(49.45, [1046.5, 1318.5, 1568, 2093]);
  whoosh(49.8, 0.9, 0.3);
  chime(55.6, [1318.5, 1568, 2093], 0.1);
  whoosh(55.9, 1.4, 0.3);
  // final chord ring
  add(56.2, 3.8, (t, k) => [220, 329.63, 440, 554.37].reduce((s, f) => s + tone(f, t), 0) * Math.exp(-2.2 * k) * 0.05 * Math.min(1, t * 8));

  for (const [k, at] of Object.entries(VOICE_AT)) {
    const v = voice[k];
    if (!v) continue;
    const a = Math.floor(at * RATE);
    for (let i = 0; i < v.length && a + i < N; i++) mix[a + i] += v[i] * 0.9;
  }

  let peak = 0;
  for (const v of mix) peak = Math.max(peak, Math.abs(v));
  const gain = peak > 0.95 ? 0.95 / peak : 1;
  const buf = Buffer.alloc(44 + N * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + N * 2, 4); buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(RATE, 24); buf.writeUInt32LE(RATE * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(N * 2, 40);
  for (let i = 0; i < N; i++) buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, mix[i] * gain)) * 32767), 44 + i * 2);
  return buf;
}
function clamp01(v) { return Math.min(1, Math.max(0, v)); }

// ---- frames ------------------------------------------------------------------------------------------------

async function render() {
  const req = createRequire(process.env.RENDER_DEPS ? path.join(process.env.RENDER_DEPS, 'noop.js') : import.meta.url);
  const puppeteer = req('puppeteer-core');
  const ffmpeg = req('ffmpeg-static');
  const browserPath = process.env.BROWSER ?? [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
  ].find((p) => fs.existsSync(p));

  const server = await serve(0);
  const port = server.address().port;
  // Start the browser ourselves and attach over DevTools: puppeteer.launch fails on recent Edge builds.
  const debugPort = 9300 + Math.floor(Math.random() * 500);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'navi-browser-'));
  const proc = spawn(browserPath, [
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--ignore-gpu-blocklist',
    '--enable-unsafe-swiftshader', `--window-size=${W},${H}`, `--user-data-dir=${profile}`,
    `--remote-debugging-port=${debugPort}`, 'about:blank',
  ], { stdio: 'ignore' });
  let browser;
  for (let i = 0; i < 50 && !browser; i++) {
    await new Promise((r) => setTimeout(r, 200));
    browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${debugPort}`, defaultViewport: null }).catch(() => undefined);
  }
  if (!browser) throw new Error(`could not attach to ${browserPath}`);
  browser.on('disconnected', () => proc.kill());
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.error('page error:', e.message));
  page.on('console', (m) => m.type() === 'error' && console.error('console:', m.text()));
  await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
  await page.goto(`http://localhost:${port}/video/index.html?render`, { waitUntil: 'networkidle0' });
  await page.waitForFunction('window.videoReady === true', { timeout: 30000 });
  await page.evaluate(() => document.fonts.ready);

  const stills = opt('--stills');
  if (stills) {
    const dir = opt('--out', path.join(os.tmpdir(), 'navi-stills'));
    fs.mkdirSync(dir, { recursive: true });
    for (const t of stills.split(',').map(Number)) {
      await page.evaluate((t) => window.renderAt(t), t);
      await page.screenshot({ path: path.join(dir, `t${t.toFixed(1).padStart(4, '0')}.png`) });
    }
    console.log('stills in', dir);
  } else {
    const out = path.resolve(opt('--out', path.join(ROOT, 'docs', 'synera', 'pitch', 'video', 'navi_pitch.mp4')));
    fs.mkdirSync(path.dirname(out), { recursive: true });
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'navi-audio-'));
    const voice = tts(
      [
        ['answer', 'The hackathon is on the third floor, in rooms oh-three twenty-one to twenty-five. Take the central elevator up, then follow the corridor.'],
        ['booked', 'Done. Six rooms booked.'],
        ['outro', 'Navi. Talk to your building.'],
      ],
      tmp,
    );
    const wav = path.join(tmp, 'soundtrack.wav');
    fs.writeFileSync(wav, soundtrack(voice));
    const ff = spawn(ffmpeg, [
      '-y', '-loglevel', 'error',
      '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
      '-i', wav,
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(FPS),
      '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', out,
    ], { stdio: ['pipe', 'inherit', 'inherit'] });
    const frames = FPS * DURATION;
    const started = Date.now();
    for (let f = 0; f < frames; f++) {
      await page.evaluate((t) => window.renderAt(t), f / FPS);
      const jpg = await page.screenshot({ type: 'jpeg', quality: 92 });
      if (!ff.stdin.write(jpg)) await new Promise((r) => ff.stdin.once('drain', r));
      if (f % 150 === 0) console.log(`frame ${f}/${frames} · ${((Date.now() - started) / 1000).toFixed(0)} s`);
    }
    ff.stdin.end();
    await new Promise((r) => ff.on('close', r));
    console.log('wrote', out);
  }
  await browser.close();
  proc.kill();
  server.close();
}
