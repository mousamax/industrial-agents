// Navi pitch video: every frame is a pure function of time. render.mjs calls window.renderAt(t) per frame.
// Geometry comes from data/MUCL/building.json; route and distances come from the building_guide tools.
import * as THREE from 'three';

const W = 1920, H = 1080;
export const DURATION = 60;
const $ = (id) => document.getElementById(id);
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const p = (t, a, b) => clamp((t - a) / (b - a));
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const easeOut = (x) => 1 - Math.pow(1 - x, 3);
const lerp = (a, b, k) => a + (b - a) * k;
const win = (t, a, b, f = 0.35) => Math.min(p(t, a, a + f), 1 - p(t, b - f, b));
function show(el, o) {
  el.style.opacity = o;
  el.style.visibility = o <= 0.001 ? 'hidden' : 'visible';
}
const typed = (text, k) => text.slice(0, Math.round(clamp(k) * text.length));

const B = await (await fetch('/data/building.json')).json();
const spaces = Object.fromEntries(B.spaces.map((s) => [s.id, s]));
const ring = (s) => {
  const pts = s.polygon.slice();
  const [a, b] = [pts[0], pts[pts.length - 1]];
  if (a[0] === b[0] && a[1] === b[1]) pts.pop();
  return pts;
};
const centroid = (id) => {
  const pts = ring(spaces[id]);
  return [pts.reduce((s, q) => s + q[0], 0) / pts.length, pts.reduce((s, q) => s + q[1], 0) / pts.length];
};
const levelOf = (id) => B.floors.find((f) => f.id === spaces[id].floor_id).level;

// ---- 3D building -------------------------------------------------------------------------------------------

const GAP = 6.5; // exploded floor spacing in scene units (metres)
const WALL = 2.4;
const toV = (x, y, level, h = 0) => new THREE.Vector3(x - 49.5, level * GAP + h, y - 13.5);

const renderer = new THREE.WebGLRenderer({ canvas: $('gl'), antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(W, H, false);
renderer.setClearColor(0x070b18, 1);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(32, W / H, 1, 2000);

const FILL = { room: 0x1d355c, corridor: 0x223150, hall: 0x27416a, stair: 0x3a3a66, lift: 0x2c4f8f, outdoor: 0x1b3a33 };
const floors = B.floors.map((f) => {
  const group = new THREE.Group();
  scene.add(group);
  const walls = new THREE.Group();
  group.add(walls);
  const slabMat = new THREE.MeshBasicMaterial({ color: 0x0c1430, transparent: true, depthWrite: false });
  const slab = new THREE.Mesh(new THREE.BoxGeometry(99.6, 0.25, 27.6), slabMat);
  slab.position.set(0, -0.2, 0);
  group.add(slab);
  const wallMat = new THREE.MeshBasicMaterial({ color: 0x6ea8ff, transparent: true, depthWrite: false, side: THREE.DoubleSide });
  const lineMat = new THREE.LineBasicMaterial({ color: 0x9cc3ff, transparent: true });
  const baseMat = new THREE.LineBasicMaterial({ color: 0x5f86c9, transparent: true });
  const quads = [], tops = [], bottoms = [];
  const fills = [];
  for (const s of B.spaces.filter((s) => s.floor_id === f.id)) {
    const pts = ring(s);
    const shape = new THREE.Shape(pts.map(([x, y]) => new THREE.Vector2(x - 49.5, 13.5 - y)));
    const geo = new THREE.ShapeGeometry(shape);
    geo.rotateX(-Math.PI / 2);
    const base = FILL[s.type] ?? FILL.room;
    const mat = new THREE.MeshBasicMaterial({ color: base, transparent: true, depthWrite: false, side: THREE.DoubleSide });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.y = 0.02;
    group.add(mesh);
    fills.push({ id: s.id, mat, base: new THREE.Color(base) });
    for (let i = 0; i < pts.length; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
      const a = [x1 - 49.5, y1 - 13.5], b = [x2 - 49.5, y2 - 13.5];
      quads.push(a[0], 0, a[1], b[0], 0, b[1], b[0], WALL, b[1], a[0], 0, a[1], b[0], WALL, b[1], a[0], WALL, a[1]);
      tops.push(a[0], WALL, a[1], b[0], WALL, b[1]);
      bottoms.push(a[0], 0.03, a[1], b[0], 0.03, b[1]);
    }
  }
  const wg = new THREE.BufferGeometry();
  wg.setAttribute('position', new THREE.Float32BufferAttribute(quads, 3));
  walls.add(new THREE.Mesh(wg, wallMat));
  const tg = new THREE.BufferGeometry();
  tg.setAttribute('position', new THREE.Float32BufferAttribute(tops, 3));
  walls.add(new THREE.LineSegments(tg, lineMat));
  const bg = new THREE.BufferGeometry();
  bg.setAttribute('position', new THREE.Float32BufferAttribute(bottoms, 3));
  group.add(new THREE.LineSegments(bg, baseMat));
  return { level: f.level, group, walls, slabMat, wallMat, lineMat, baseMat, fills };
});
const fillOf = Object.fromEntries(floors.flatMap((f) => f.fills.map((x) => [x.id, { ...x, floor: f }])));

// Route as the building_guide `route` tool returned it (space_path), drawn through the shared openings.
const ROUTE_PATH = ['F0-HALL-SE', 'F0-CORR-MAIN', 'F0-LIFT-C', 'F3-LIFT-C', 'F3-CORR-MAIN', 'F3-CORR-W', 'F3-RM-0321'];
const HACK_ROOMS = ['F3-RM-0321', 'F3-RM-0322', 'F3-RM-0323', 'F3-RM-0324', 'F3-RM-0325'];
const opening = (a, b) => B.openings.find((o) => o.space_ids.includes(a) && o.space_ids.includes(b));
function routePoints(path, entrance) {
  const pts = [];
  const lift = (id) => spaces[id].type === 'lift';
  if (entrance) pts.push(toV(entrance[0], entrance[1] - 4, 0, 0.4), toV(entrance[0], entrance[1], 0, 0.4));
  else pts.push(toV(...centroid(path[0]), levelOf(path[0]), 0.4));
  for (let i = 0; i < path.length - 1; i++) {
    const [a, b] = [path[i], path[i + 1]];
    if (lift(a) && lift(b)) {
      pts.push(toV(...centroid(a), levelOf(a), 0.4), toV(...centroid(b), levelOf(b), 0.4));
      continue;
    }
    const o = opening(a, b);
    if (o) pts.push(toV(o.position[0], o.position[1], levelOf(a), 0.4));
  }
  pts.push(toV(...centroid(path[path.length - 1]), levelOf(path[path.length - 1]), 0.4));
  return pts;
}
function makeRoute(points, color) {
  const path = new THREE.CurvePath();
  for (let i = 0; i < points.length - 1; i++) path.add(new THREE.LineCurve3(points[i], points[i + 1]));
  const geo = new THREE.TubeGeometry(path, 600, 0.38, 10, false);
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true }));
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.95, 24, 16), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true }));
  const halo = new THREE.Mesh(new THREE.SphereGeometry(2.2, 24, 16), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.25, depthWrite: false }));
  scene.add(mesh, head, halo);
  const total = geo.index.count;
  return {
    set(k, o) {
      geo.setDrawRange(0, Math.floor((total / 6) * clamp(k)) * 6);
      mesh.material.opacity = o;
      const at = path.getPointAt(clamp(k, 0.0001, 1));
      head.position.copy(at);
      halo.position.copy(at);
      head.material.opacity = o;
      halo.material.opacity = 0.25 * o;
      mesh.visible = head.visible = halo.visible = o > 0.001 && k > 0;
    },
  };
}
const entranceOpening = B.openings.find((o) => o.space_ids.includes('F0-HALL-SE') && o.space_ids.includes('outside') && o.position[0] === 55) ?? B.openings.find((o) => o.space_ids.includes('F0-HALL-SE') && o.space_ids.includes('outside'));
const guestRoute = makeRoute(routePoints(ROUTE_PATH, entranceOpening.position), 0xfbbf24);

// ---- labels projected from 3D ------------------------------------------------------------------------------

const labelEls = {};
function label(key, text, pos, o, cls = '') {
  let el = labelEls[key];
  if (!el) {
    el = document.createElement('div');
    el.className = 'label3d ' + cls;
    $('labels').appendChild(el);
    labelEls[key] = el;
  }
  el.textContent = text;
  const v = pos.clone().project(camera);
  el.style.left = ((v.x + 1) / 2) * W + 'px';
  el.style.top = ((1 - v.y) / 2) * H + 'px';
  el.style.opacity = o;
}

// ---- camera ------------------------------------------------------------------------------------------------

const reception = centroid('F0-HALL-SE');
const recV = toV(reception[0], reception[1], 0);
const KEYS = [
  // t, position, target
  [0.0, [0, 125, -2], [0, 0, 0]],
  [1.6, [0, 118, -8], [0, 0, 0]],
  [3.4, [-74, 70, -108], [2, 10, 0]],
  [5.0, [-60, 64, -112], [4, 11, 0]],
  [7.6, [recV.x - 16, 17, recV.z - 30], [recV.x, 1, recV.z]],
  [25.0, [62, 62, -84], [4, 10, 0]],
  [30.0, [34, 56, -94], [4, 12, 0]],
  [50.0, [-58, 62, -86], [0, 10, 0]],
  [56.0, [-18, 60, -98], [2, 10, 0]],
  [60.0, [12, 60, -98], [2, 10, 0]],
];
const JUMPS = new Set([25.0, 50.0]); // hard cuts: no interpolation into these keys
function cameraAt(t) {
  let i = KEYS.findIndex((k) => k[0] > t);
  if (i === -1) i = KEYS.length - 1;
  if (i === 0) i = 1;
  const [ta, pa, qa] = KEYS[i - 1], [tb, pb, qb] = KEYS[i];
  const k = JUMPS.has(tb) ? 0 : ease(p(t, ta, tb));
  const pos = pa.map((v, j) => lerp(v, pb[j], k)), tgt = qa.map((v, j) => lerp(v, qb[j], k));
  camera.position.set(...pos);
  camera.lookAt(...tgt);
}

// ---- 3D state per time -------------------------------------------------------------------------------------

const TEAL = new THREE.Color(0x2dd4bf), AMBER = new THREE.Color(0xfbbf24);
function floorOpacity(level, t) {
  if (t < 7.8) {
    const arrive = level === 0 ? 1 : easeOut(p(t, 2.0 + level * 0.35, 2.6 + level * 0.35));
    const dim = level === 0 ? 1 : lerp(1, 0.06, p(t, 5.0, 6.6));
    return arrive * dim;
  }
  if (t < 40) return level === 0 || level === 3 ? 0.95 : 0.3;
  return level === 1 || level === 3 ? 0.95 : 0.22;
}
function floorLift(level, t) {
  if (t >= 7.8 || level === 0) return 0;
  return (1 - easeOut(p(t, 2.0 + level * 0.35, 2.8 + level * 0.35))) * 22;
}
function update3D(t) {
  cameraAt(t);
  const wallsUp = t < 7.8 ? ease(p(t, 0.8, 2.4)) : 1;
  for (const f of floors) {
    const o = floorOpacity(f.level, t);
    f.group.position.y = f.level * GAP + floorLift(f.level, t);
    f.walls.scale.y = Math.max(0.01, wallsUp);
    f.group.visible = o > 0.01;
    f.slabMat.opacity = 0.55 * o;
    f.wallMat.opacity = 0.13 * o;
    f.lineMat.opacity = 0.6 * o;
    f.baseMat.opacity = 0.45 * o;
    for (const x of f.fills) {
      x.mat.color.copy(x.base);
      x.mat.opacity = 0.55 * o;
    }
  }
  const glow = (id, color, k, max = 0.95) => {
    const x = fillOf[id];
    if (!x || k <= 0) return;
    const pulse = 0.85 + 0.15 * Math.sin(t * 5);
    x.mat.color.copy(x.base).lerp(color, k);
    x.mat.opacity = lerp(x.mat.opacity, max * pulse, k);
  };
  if (t < 7.8) glow('F0-HALL-SE', TEAL, 0.7 * p(t, 5.2, 6.4), 0.6);
  if (t >= 25 && t < 30) for (const id of HACK_ROOMS) glow(id, TEAL, p(t, 27.2, 28.2));
  if (t >= 50) for (const id of ['F1-RM-ARENA', ...HACK_ROOMS]) glow(id, AMBER, p(t, 50.6, 51.6));

  if (t >= 25 && t < 30) guestRoute.set(ease(p(t, 25.4, 29.2)), 1);
  else if (t >= 53 && t < 60) guestRoute.set(ease(p(t, 53.0, 55.8)), 1 - p(t, 57.5, 58.5));
  else guestRoute.set(0, 0);
}
function updateLabels(t) {
  const ar = centroid('F1-RM-ARENA'), r21 = centroid('F3-RM-0321'), lift = centroid('F0-LIFT-C');
  const hackMid = [(centroid('F3-RM-0321')[0] + centroid('F3-RM-0325')[0]) / 2, r21[1]];
  label('rec', 'Empfang', toV(reception[0], reception[1], 0, 3), t < 7.8 ? win(t, 5.6, 7.4) : win(t, 25.2, 29.8));
  label('lift', 'Aufzug Mitte', toV(lift[0], lift[1], 1.6, 3), win(t, 26.4, 29.8));
  label('hack', 'Hackathon · Besprechung 03.21–03.25', toV(hackMid[0], hackMid[1], 3, 3.2), win(t, 27.8, 29.9));
  label('arena', 'Arena · kickoff', toV(ar[0], ar[1], 1, 3), win(t, 51.0, 56.6), 'amber');
  label('teams', '03.21–03.25 · team rooms', toV(hackMid[0], hackMid[1], 3, 3.2), win(t, 51.4, 56.6), 'amber');
}

// ---- 2D scenes ---------------------------------------------------------------------------------------------

const mic = $('mic');
for (let i = 0; i < 34; i++) mic.appendChild(document.createElement('i'));
const micLabel = document.createElement('b');
mic.appendChild(micLabel);

// Floor-3 mini map on the tablet, built from the same polygons.
const NS = 'http://www.w3.org/2000/svg';
const mm = $('minimap');
const mmFill = {};
for (const s of B.spaces.filter((s) => s.floor_id === 'F3')) {
  const el = document.createElementNS(NS, 'polygon');
  el.setAttribute('points', ring(s).map((q) => q.join(',')).join(' '));
  el.setAttribute('fill', s.type === 'lift' ? '#60a5fa' : s.type === 'corridor' ? '#1c2a4a' : '#16223f');
  el.setAttribute('stroke', '#4b6aa8');
  el.setAttribute('stroke-width', '0.18');
  mm.appendChild(el);
  mmFill[s.id] = el;
}
const mmRoutePts = routePoints(['F3-LIFT-C', 'F3-CORR-MAIN', 'F3-CORR-W', 'F3-RM-0321']).map((v) => [v.x + 49.5, v.z + 13.5]);
const mmRoute = document.createElementNS(NS, 'polyline');
mmRoute.setAttribute('points', mmRoutePts.map((q) => q.join(',')).join(' '));
mmRoute.setAttribute('fill', 'none');
mmRoute.setAttribute('stroke', '#fbbf24');
mmRoute.setAttribute('stroke-width', '0.6');
mmRoute.setAttribute('stroke-linecap', 'round');
mmRoute.setAttribute('stroke-linejoin', 'round');
mm.appendChild(mmRoute);
const mmLen = mmRoutePts.slice(1).reduce((s, q, i) => s + Math.hypot(q[0] - mmRoutePts[i][0], q[1] - mmRoutePts[i][1]), 0);
mmRoute.setAttribute('stroke-dasharray', `${mmLen} ${mmLen}`);
const mmText = document.createElementNS(NS, 'text');
const r21 = centroid('F3-RM-0321');
mmText.setAttribute('x', (r21[0] + centroid('F3-RM-0325')[0]) / 2);
mmText.setAttribute('y', r21[1] + 4.6);
mmText.setAttribute('text-anchor', 'middle');
mmText.setAttribute('font-size', '1.5');
mmText.setAttribute('font-weight', '700');
mmText.setAttribute('fill', '#5eead4');
mmText.textContent = '03.21–03.25 Hackathon';
mm.appendChild(mmText);

function toolRow(el, t, start, run = 1.1) {
  show(el, easeOut(p(t, start, start + 0.3)));
  el.style.transform = `translateY(${(1 - easeOut(p(t, start, start + 0.3))) * 16}px)`;
  const done = t >= start + run;
  el.classList.toggle('done', done);
  const st = el.querySelector('.st');
  st.textContent = done ? '✓' : '';
  st.style.transform = done ? '' : `rotate(${t * 540}deg)`;
  el.querySelector('.res').style.opacity = p(t, start + run, start + run + 0.25);
}

function cursor(el, t, path) {
  // path: [[t, x, y], ...]
  let i = path.findIndex((q) => q[0] > t);
  if (i === -1) i = path.length - 1;
  if (i === 0) i = 1;
  const [ta, xa, ya] = path[i - 1], [tb, xb, yb] = path[i];
  const k = ease(p(t, ta, tb));
  el.style.left = lerp(xa, xb, k) + 'px';
  el.style.top = lerp(ya, yb, k) + 'px';
}
function ripple(el, t, at, x, y) {
  const k = p(t, at, at + 0.5);
  el.style.left = x + 'px';
  el.style.top = y + 'px';
  el.style.opacity = k > 0 && k < 1 ? 1 - k : 0;
  el.style.transform = `scale(${0.3 + k})`;
}

const NAVI_ANSWER =
  'The <strong>Industrial Agents Hackathon</strong> is on the <strong>3rd floor</strong>, rooms <strong>03.21–03.25</strong>. ' +
  'Take the central elevator (Aufzug Mitte) up to floor 3, then follow the corridor to the meeting rooms. About 60 m.';
const NAVI_PLAIN = NAVI_ANSWER.replace(/<[^>]+>/g, '');
function typedHtml(html, plain, k) {
  // reveal `plain` progressively while keeping the <strong> tags of `html`
  let n = Math.round(clamp(k) * plain.length), out = '', i = 0;
  while (i < html.length && n > 0) {
    if (html[i] === '<') {
      const j = html.indexOf('>', i);
      out += html.slice(i, j + 1);
      i = j + 1;
    } else {
      out += html[i++];
      n--;
    }
  }
  const opens = (out.match(/<strong>/g) || []).length, closes = (out.match(/<\/strong>/g) || []).length;
  return out + (opens > closes ? '</strong>' : '');
}

const THINK = 'Book one room for ~60 people (kickoff + pitches) and breakout rooms for 12 teams on Sat 24 Oct. Everything step-free, coffee close by.';

function update2D(t) {
  // reception
  const recO = Math.min(p(t, 7.0, 7.8), 1 - p(t, 13.0, 13.7));
  show($('reception'), recO);
  if (recO > 0) {
    const walk = ease(p(t, 7.2, 9.2));
    const gx = lerp(-250, 930, walk);
    const stepping = t > 7.2 && t < 9.2;
    const sw = stepping ? Math.sin(t * 14) * 22 : 0;
    $('guest').setAttribute('transform', `translate(${gx},${stepping ? -Math.abs(Math.sin(t * 14)) * 8 : 0})`);
    $('legL').setAttribute('transform', `rotate(${sw} 0 660)`);
    $('legR').setAttribute('transform', `rotate(${-sw} 0 660)`);
    const bub = win(t, 9.0, 11.6, 0.2);
    $('bubble').setAttribute('opacity', bub);
    $('bubble').setAttribute('transform', `translate(${(1 - easeOut(p(t, 9.0, 9.25))) * 30}, 0)`);
    $('bubbleText').textContent = typed("Hi, where's the hackathon?", p(t, 9.05, 9.9));
    const point = ease(p(t, 11.2, 11.8));
    $('recArm').setAttribute('transform', `translate(1428,485) rotate(${lerp(-80, -16, point)})`);
    $('recHead').setAttribute('transform', `rotate(${14 + Math.sin(t * 6) * 3} 1483 400)`);
    $('notes').setAttribute('transform', `translate(0, ${-((t * 30) % 40)})`);
    $('notes').setAttribute('opacity', 0.5 + 0.5 * Math.sin(t * 4));
    $('tabGlow').setAttribute('opacity', 0.35 + 0.5 * p(t, 11.6, 12.4) * (0.75 + 0.25 * Math.sin(t * 8)));
    const zoom = ease(p(t, 12.5, 13.7));
    const s = lerp(1, 5, zoom);
    $('recWorld').setAttribute('transform', `translate(${1195 - 1195 * s + (960 - 1195) * zoom}, ${535 - 535 * s + (540 - 535) * zoom}) scale(${s})`);
  }

  // tablet close-up
  const tabO = Math.min(p(t, 13.2, 13.8), 1 - p(t, 25.0, 25.5));
  show($('tablet'), tabO);
  if (tabO > 0) {
    const listening = t < 15.2;
    [...mic.querySelectorAll('i')].forEach((bar, i) => {
      const amp = listening && t > 13.6 ? 0.25 + 0.75 * Math.abs(Math.sin(t * 9 + i * 0.7) * Math.sin(t * 3.3 + i * 0.31)) : 0.08;
      bar.style.height = 8 + amp * 58 + 'px';
      bar.style.opacity = listening ? 1 : 0.35;
    });
    micLabel.textContent = listening ? 'Listening…' : 'Heard you ✓';
    $('tUser').textContent = '🎙 ' + typed("Where's the hackathon?", p(t, 13.9, 15.0));
    toolRow($('t1'), t, 15.4);
    toolRow($('t2'), t, 17.0);
    toolRow($('t3'), t, 18.6);
    show($('tAns'), p(t, 20.6, 20.9));
    $('tAns').innerHTML = typedHtml(NAVI_ANSWER, NAVI_PLAIN, p(t, 20.7, 24.0));
    const hk = p(t, 18.0, 18.8);
    for (const id of HACK_ROOMS) mmFill[id].setAttribute('fill', hk > 0 ? `rgba(45,212,191,${0.3 + 0.6 * hk * (0.85 + 0.15 * Math.sin(t * 5))})` : '#16223f');
    mmText.setAttribute('opacity', hk);
    mmRoute.setAttribute('stroke-dashoffset', mmLen * (1 - ease(p(t, 19.7, 21.0))));
  }

  // desk
  const deskO = Math.min(p(t, 30.0, 30.4), 1 - p(t, 32.8, 33.4));
  show($('desk'), deskO);
  if (deskO > 0) {
    const toastK = easeOut(p(t, 30.9, 31.3));
    $('mailToast').setAttribute('opacity', toastK);
    $('mailToast').setAttribute('transform', `translate(${(1 - toastK) * 40}, 0)`);
    $('steam').setAttribute('transform', `translate(0, ${-((t * 20) % 20)})`);
    const zoom = ease(p(t, 32.2, 33.4));
    const s = lerp(1, 3.2, zoom);
    $('deskWorld').setAttribute('transform', `translate(${690 - 690 * s + (960 - 690) * zoom}, ${390 - 390 * s + (540 - 390) * zoom}) scale(${s})`);
  }
  show($('stamp'), win(t, 30.2, 32.2, 0.25));
  $('stamp').style.transform = `scale(${lerp(1.6, 1, easeOut(p(t, 30.2, 30.5)))})`;

  // mail
  const mailO = Math.min(p(t, 32.9, 33.5), 1 - p(t, 38.4, 38.9));
  show($('mail'), mailO);
  if (mailO > 0) {
    ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].forEach((id, i) => {
      $(id).style.backgroundSize = `${ease(p(t, 33.7 + i * 0.32, 34.1 + i * 0.32)) * 100}% 100%`;
    });
    const rk = easeOut(p(t, 35.9, 36.3));
    $('reply').style.transform = `translateY(${(1 - rk) * 300}px)`;
    $('reply').style.opacity = rk;
    const txt = typed('@Navi please handle this.', p(t, 36.4, 37.5));
    $('rChip').innerHTML = txt.length >= 5 ? '<span class="chip">@Navi</span>' : txt;
    $('rText').textContent = txt.length >= 5 ? txt.slice(5) : '';
    $('caret').style.opacity = Math.floor(t * 2.5) % 2 ? 1 : 0;
    const sent = t >= 38.0;
    $('sendBtn').textContent = sent ? 'Sent ✓' : 'Send';
    $('sendBtn').style.background = sent ? '#10b981' : '#3b82f6';
    const cur = $('cur1');
    cur.style.opacity = win(t, 37.2, 38.6, 0.2);
    cursor(cur, t, [[37.2, 1300, 700], [37.9, 1690, 958], [40, 1690, 958]]);
    ripple($('rip1'), t, 38.0, 1710, 978);
  }

  // console
  const conO = Math.min(p(t, 38.5, 39.0), 1 - p(t, 45.8, 46.3));
  show($('console'), conO);
  if (conO > 0) {
    $('cQ').textContent = typed(THINK, p(t, 38.9, 40.6));
    ['n1', 'n2', 'n3', 'n4', 'n5', 'n6', 'n7'].forEach((id, i) => {
      const k = easeOut(p(t, 39.6 + i * 0.22, 39.9 + i * 0.22));
      show($(id), k);
      $(id).style.transform = `scale(${lerp(0.8, 1, k)})`;
    });
    ['c1', 'c2', 'c3', 'c4', 'c5'].forEach((id, i) => toolRow($(id), t, 40.4 + i * 1.0, 0.8));
  }

  // options
  const optO = Math.min(p(t, 45.9, 46.4), 1 - p(t, 50.0, 50.6));
  show($('options'), optO);
  if (optO > 0) {
    ['o1', 'o2', 'o3'].forEach((id, i) => {
      const k = easeOut(p(t, 46.2 + i * 0.25, 46.7 + i * 0.25));
      show($(id), k);
      $(id).style.transform = `translateY(${(1 - k) * 60}px)`;
    });
    const booked = t >= 49.4;
    $('o1').style.boxShadow = `0 0 ${60 + 30 * Math.sin(t * 5)}px rgba(45,212,191,${booked ? 0.6 : 0.3})`;
    $('bookA').textContent = booked ? '✓ Booked' : 'Book A';
    $('bookA').style.background = booked ? '#10b981' : '';
    $('bookA').style.color = booked ? '#fff' : '';
    const cur = $('cur2');
    cur.style.opacity = win(t, 48.2, 50.2, 0.2);
    cursor(cur, t, [[48.2, 1100, 980], [49.2, 420, 795], [51, 420, 795]]);
    ripple($('rip2'), t, 49.4, 430, 810);
  }

  // toast after booking
  show($('toast'), win(t, 50.7, 53.2, 0.3));
  $('toast').innerHTML = '✓ 6 rooms booked for Sat 24 Oct<small>Calendar updated · guest directions published to the front-desk tablet</small>';

  // captions over 3D
  const caps = [
    [0.6, 5.0, 'Munich Urban Colab, rebuilt from its floor plans', '5 floors · 198 spaces · 224 doors'],
    [25.6, 30.0, 'Lobby to room with one question', 'No staff needed · 58.7 m route, step-free'],
    [53.2, 56.4, 'Booked by one sentence. Found by one question.', 'One building model for facility managers and guests'],
  ];
  const c = caps.find(([a, b]) => t >= a && t < b);
  show($('caption'), c ? win(t, c[0], c[1], 0.4) : 0);
  if (c) {
    $('capMain').textContent = c[2];
    $('capSub').textContent = c[3];
  }

  // end card
  show($('endcard'), p(t, 56.2, 57.0));
  $('endcard').querySelector('.word').style.transform = `scale(${lerp(0.9, 1, easeOut(p(t, 56.2, 57.2)))})`;

  // dips to black between acts
  const dips = [[7.8, 0], [25.2, 0.25], [29.7, 0.35], [49.9, 0.3], [59.4, 0]];
  let f = 0;
  for (const [at, d] of dips) f = Math.max(f, d ? win(t, at - d, at + d, d) : 0);
  f = Math.max(f, p(t, 59.3, 60));
  $('fade').style.opacity = f;
}

window.renderAt = (t) => {
  const in3D = t < 7.8 || (t >= 25 && t < 30.2) || t >= 50;
  $('gl').style.visibility = in3D ? 'visible' : 'hidden';
  $('labels').style.visibility = in3D ? 'visible' : 'hidden';
  if (in3D) {
    update3D(t);
    renderer.render(scene, camera);
  }
  updateLabels(t);
  update2D(t);
};

window.renderAt(0);
window.videoReady = true;

// Live preview: play in real time unless the renderer drives frames (?render) or a still is requested (?t=12).
const q = new URLSearchParams(location.search);
if (q.has('t')) window.renderAt(parseFloat(q.get('t')));
else if (!q.has('render')) {
  const t0 = performance.now();
  const tick = () => {
    window.renderAt(((performance.now() - t0) / 1000) % DURATION);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
