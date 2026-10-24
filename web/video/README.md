# Pitch video (Navi, 60 s)

Storyboard and script: `docs/synera/pitch/video_storyboard.md`.

- `index.html`: the 2D scenes (reception, tablet, mail, agent console, options, end card) as HTML/SVG.
- `scene.js`: the MUCL 3D model from `data/MUCL/building.json` (three.js) and the timeline. Every frame is
  a pure function of time `window.renderAt(t)`, so renders are deterministic.
- `render.mjs`: serves the page, screenshots 1800 frames in headless Edge/Chrome, mixes a synthesized
  soundtrack plus Windows text-to-speech for Navi's lines, and encodes with ffmpeg.

```bash
node web/video/render.mjs --serve
```
Live preview at http://localhost:5199/video/ (add `?t=27.5` to freeze a frame).

Rendering needs two packages kept outside the repo:

```bash
npm i --prefix ../render-deps puppeteer-core@23.11.1 ffmpeg-static@5.2.0
```

```bash
RENDER_DEPS=../render-deps/node_modules node web/video/render.mjs
```
Writes `docs/synera/pitch/video/navi_pitch.mp4` (about 5–10 min). `--stills 3,12,40 --out <dir>` writes
PNG stills instead.
