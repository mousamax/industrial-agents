// Dev-only demo of the 3D engine: /map3d-demo.html?b=<building>&pitch=50&k=30&floor=<level|id>&detail=2
// Optional: cx, cy (centre, scene m), bearing, hl=<id,id>, sel=space:<id>|asset:<id>, route=1 (a made-up route
// through the floor's corridors, for looking at the route tube), lower=0, gap=<floorGapScale>, info=0.
// Drag pans, wheel zooms, shift-drag (or right-drag) tilts, click logs the pick.
import type { Scene, ShowRoute } from '../../api/types'
import { buildIndex, type SceneIndex } from '../../lib/sceneIndex'
import { floorGeo } from '../map/geometry'
import { DEFAULT_3D } from './devSettings'
import { createMap3D } from './Map3D'
import type { Map3DSettings, View3D } from './types'

const q = new URL(location.href).searchParams
const num = (k: string, d: number) => (q.has(k) ? Number(q.get(k)) : d)
const info = document.getElementById('info')!
const canvas = document.getElementById('map') as HTMLCanvasElement

function fakeRoute(index: SceneIndex, floorId: string): ShowRoute | null {
  const f = index.floorById.get(floorId)!
  const below = [...index.floors].reverse().find((x) => x.level < f.level)
  const pick = (fid: string) => {
    const sp = (index.spacesByFloor.get(fid) ?? []).filter((s) => /corridor|hall/i.test(s.type))
    const list = (sp.length ? sp : index.spacesByFloor.get(fid) ?? []).slice(0, 6)
    return list.map((s) => [s.anchor[0], s.anchor[1]] as [number, number]).sort((a, b) => a[0] - b[0])
  }
  const segments = []
  if (below) segments.push({ floor_id: below.id, points: pick(below.id).slice(0, 3) })
  segments.push({ floor_id: floorId, points: pick(floorId) })
  return { kind: 'route', segments, length_m: 0, space_ids: [] }
}

async function main() {
  const b = q.get('b') ?? 'proc-factory-dev-003'
  const t0 = performance.now()
  const scene = (await (await fetch(`/api/buildings/${b}/scene`)).json()) as Scene
  const index = buildIndex(scene)
  const tFetch = performance.now() - t0
  const fq = q.get('floor')
  const floor =
    index.floors.find((f) => f.id === fq || String(f.level) === fq) ?? index.floors.find((f) => f.level === 0) ?? index.floors[0]
  const geo = floorGeo(index, floor.id)
  const bb = geo.bbox! // SVG (flipped) coords
  const w = window.innerWidth
  const h = window.innerHeight
  const fitK = Math.min(w / (bb.maxX - bb.minX), h / (bb.maxY - bb.minY)) * 0.9
  const view: View3D = {
    center: [num('cx', (bb.minX + bb.maxX) / 2), num('cy', -(bb.minY + bb.maxY) / 2)],
    k: num('k', fitK),
    pitchDeg: num('pitch', 45),
    bearingDeg: num('bearing', 0),
  }
  const settings: Map3DSettings = {
    ...DEFAULT_3D,
    objectDetail: num('detail', 2) as 0 | 1 | 2,
    showLowerFloors: q.get('lower') !== '0',
    floorGapScale: num('gap', DEFAULT_3D.floorGapScale),
    wallHeightM: num('wall', DEFAULT_3D.wallHeightM),
  }

  const engine = createMap3D(canvas)
  engine.setSize(w, h, window.devicePixelRatio)
  engine.setSettings(settings)
  engine.setScene(index)
  engine.setFloor(floor.id)
  engine.setView(view)
  if (q.get('hl')) engine.setHighlight(q.get('hl')!.split(','))
  const sel = q.get('sel')
  if (sel) {
    const [kind, ...rest] = sel.split(':')
    engine.setSelection({ kind: kind as 'space' | 'asset', id: rest.join(':') })
  }
  if (q.get('route') === '1') engine.setRoute(fakeRoute(index, floor.id))

  // Build + first frame timing (synchronous render through the rAF the engine schedules).
  const tb = performance.now()
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  const tFirst = performance.now() - tb
  let frames = 0
  let tFrames = 0
  const measure = () => {
    const ts = performance.now()
    engine.requestRender()
    requestAnimationFrame(() => {
      tFrames += performance.now() - ts
      frames++
      if (frames < 20) measure()
      else show()
    })
  }
  const show = () => {
    if (q.get('info') === '0') return void (info.style.display = 'none')
    const assets = scene.assets.filter((a) => a.floor_id === floor.id).length
    const stats = (engine as unknown as { __stats: () => { ms: number; calls: number; triangles: number } }).__stats
    const runs = [stats(), stats(), stats(), stats(), stats()]
    const st = runs[runs.length - 1]
    const avg = runs.slice(1).reduce((a, r) => a + r.ms, 0) / (runs.length - 1)
    // Projection self-check: the centre lands mid-screen and 10 m east/north measure 10 k px at pitch 0.
    const c0 = engine.project(view.center, floor.elevation_m)
    const ce = engine.project([view.center[0] + 10, view.center[1]], floor.elevation_m)
    const cn = engine.project([view.center[0], view.center[1] + 10], floor.elevation_m)
    const back = engine.unproject(c0.x + 37, c0.y - 21, floor.elevation_m)
    const fwd = back ? engine.project(back, floor.elevation_m) : null
    info.textContent =
      `centre -> (${c0.x.toFixed(1)}, ${c0.y.toFixed(1)}) of (${w / 2}, ${h / 2}) · east 10 m = ${((ce.x - c0.x) / 10).toFixed(2)} k-px · ` +
      `north 10 m = ${((c0.y - cn.y) / 10).toFixed(2)} k-px · unproject/project round trip err ${fwd ? Math.hypot(fwd.x - c0.x - 37, fwd.y - c0.y + 21).toFixed(3) : 'n/a'} px\n` +
      `sync frame ${avg.toFixed(1)} ms · ${st.calls} draw calls · ${(st.triangles / 1000).toFixed(0)}k triangles\n` +
      `${b} · ${floor.name} · ${(index.spacesByFloor.get(floor.id) ?? []).length} spaces · ${assets} assets\n` +
      `k ${view.k.toFixed(1)} pitch ${view.pitchDeg} · fetch ${tFetch.toFixed(0)} ms · build+first frame ${tFirst.toFixed(0)} ms · ` +
      `frame (rAF) ${(tFrames / Math.max(frames, 1)).toFixed(1)} ms`
  }
  measure()

  // Interaction for manual testing.
  let drag: { x: number; y: number; tilt: boolean } | null = null
  canvas.addEventListener('contextmenu', (e) => e.preventDefault())
  canvas.addEventListener('pointerdown', (e) => {
    drag = { x: e.clientX, y: e.clientY, tilt: e.shiftKey || e.button === 2 }
    canvas.setPointerCapture(e.pointerId)
  })
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return
    if (drag.tilt) view.pitchDeg = Math.max(0, Math.min(settings.maxPitchDeg, view.pitchDeg - (e.clientY - drag.y) * 0.3))
    else {
      const elev = floor.elevation_m
      const a = engine.unproject(drag.x, drag.y, elev)
      const c = engine.unproject(e.clientX, e.clientY, elev)
      if (a && c) view.center = [view.center[0] - (c[0] - a[0]), view.center[1] - (c[1] - a[1])]
    }
    drag.x = e.clientX
    drag.y = e.clientY
    engine.setView(view)
  })
  canvas.addEventListener('pointerup', (e) => {
    if (drag && Math.abs(e.clientX - drag.x) < 3 && Math.abs(e.clientY - drag.y) < 3) {
      const hit = engine.pick(e.clientX, e.clientY)
      console.log('pick', hit)
      if (hit) engine.setSelection(hit)
    }
    drag = null
  })
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault()
    const before = engine.unproject(e.clientX, e.clientY, floor.elevation_m)
    view.k *= Math.exp(-e.deltaY * 0.0015)
    engine.setView(view)
    const after = engine.unproject(e.clientX, e.clientY, floor.elevation_m)
    if (before && after) {
      view.center = [view.center[0] + before[0] - after[0], view.center[1] + before[1] - after[1]]
      engine.setView(view)
    }
  }, { passive: false })
  window.addEventListener('resize', () => engine.setSize(window.innerWidth, window.innerHeight, window.devicePixelRatio))
  ;(window as unknown as { map3d: unknown }).map3d = { engine, view, index }
}

main().catch((e) => {
  info.textContent = String(e?.stack ?? e)
})
