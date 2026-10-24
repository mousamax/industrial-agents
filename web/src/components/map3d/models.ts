// Low-poly procedural asset models, built from a few boxes / cylinders / spheres.
// Every model is authored in a canonical frame (scene metres): x across the width, y across the depth with
// the BACK of the object at +y (chair back, headboard, toilet tank, the wall side of a shelf), z up, footprint
// centred on the origin. Map3D rotates and places instances; assets.ts decides which side is the back.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { CATEGORY_STYLE, DEFAULT_STYLE } from './palette'

export interface Sink {
  /** Axis-aligned box from (x0, y0, z0) to (x1, y1, z1). */
  box(x0: number, x1: number, y0: number, y1: number, z0: number, z1: number, c: string): void
  /** Vertical cylinder. */
  cyl(cx: number, cy: number, r: number, z0: number, z1: number, c: string, seg?: number): void
  /** Horizontal cylinder along x. */
  cylX(x0: number, x1: number, cy: number, cz: number, r: number, c: string): void
  ball(cx: number, cy: number, cz: number, r: number, c: string): void
}

type Build = (W: number, D: number, s: Sink) => void // W, D = half width / half depth

export interface ModelSpec {
  /** Height of the simple box (objectDetail 1) and of the bounding volume. */
  h: number
  /** Main colour (objectDetail 1 box). */
  c: string
  /** Which side is the back: 'wall' = side nearest a wall, 'table' = away from the nearest table, else +y. */
  back?: 'wall' | 'table'
  build: Build
}

// ---------- palette ----------
const WOOD = '#d9c6a5'
const WOOD_DARK = '#b59c78'
const DARK = '#4a4f55'
const GREY = '#9aa0a6'
const LIGHT = '#e3e6ea'
const WHITE = '#f6f7f8'
const METAL = '#c3c8cf'
const MACHINE = '#d6dae0'
const MACHINE_TOP = '#e9ecef'
const GLASS = '#8ea4ba'
const MACHINE_ACCENT = '#5b8bd0'
const ORANGE = '#ef9a4a'
const BEAM = '#4f78b0'
const CARGO = '#d8b27e'
const GREEN = '#7cb062'
const POT = '#b08868'
const RED = '#d8473c'
const YELLOW = '#f2b832'
const FABRIC = '#9fb2c6'
const CHAIR = '#6b7178'
const LINEN = '#ffffff'
const DUVET = '#d5e3f5'
const CABINET = '#dfe2e6'
const BOOKS = ['#c9b7a3', '#a9bccd', '#c7cfb6']

// ---------- building blocks ----------
function legs(s: Sink, W: number, D: number, t: number, z0: number, z1: number, c: string) {
  for (const sx of [-1, 1])
    for (const sy of [-1, 1]) {
      const x = sx * (W - t / 2)
      const y = sy * (D - t / 2)
      s.box(x - t / 2, x + t / 2, y - t / 2, y + t / 2, z0, z1, c)
    }
}

function table(h: number, top: string, leg = GREY): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, h - 0.04, h, top)
    legs(s, W - 0.03, D - 0.03, 0.05, 0, h - 0.04, leg)
  }
}

function cabinet(h: number, c: string, opts: { seams?: number; drawers?: number } = {}): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, 0.06, h, c)
    s.box(-W + 0.02, W - 0.02, -D + 0.02, D - 0.02, 0, 0.06, DARK) // plinth
    const seam = shade(c, 0.78)
    if (opts.seams) for (let i = 1; i < opts.seams + 1; i++) {
      const x = -W + (2 * W * i) / (opts.seams + 1)
      s.box(x - 0.006, x + 0.006, -D - 0.006, -D, 0.1, h - 0.04, seam)
    }
    if (opts.drawers) for (let i = 1; i < opts.drawers; i++) {
      const z = 0.06 + ((h - 0.06) * i) / opts.drawers
      s.box(-W + 0.02, W - 0.02, -D - 0.006, -D, z - 0.006, z + 0.006, seam)
    }
  }
}

function shelf(h: number, frame: string, levels: number, fill: 'books' | 'cargo' | 'none'): Build {
  return (W, D, s) => {
    const t = 0.03
    s.box(-W, -W + t, -D, D, 0, h, frame)
    s.box(W - t, W, -D, D, 0, h, frame)
    s.box(-W, W, D - 0.015, D, 0, h, shade(frame, 0.92))
    for (let i = 0; i <= levels; i++) {
      const z = i === levels ? h - t : (i * h) / levels
      s.box(-W + t, W - t, -D, D, z, z + t, frame)
      if (fill === 'books' && i < levels) {
        const zh = (h / levels) * 0.7
        s.box(-W + t + 0.02, W - t - 0.04, -D + 0.05, D - 0.03, z + t, z + t + zh, BOOKS[i % BOOKS.length])
      }
    }
  }
}

function chair(seat: string, frame = GREY): Build {
  return (W, D, s) => {
    legs(s, W * 0.85, D * 0.85, 0.035, 0, 0.42, frame)
    s.box(-W * 0.9, W * 0.9, -D * 0.9, D * 0.85, 0.42, 0.47, seat)
    s.box(-W * 0.88, W * 0.88, D * 0.68, D * 0.86, 0.47, 0.88, seat)
  }
}

function sofa(c: string, backH = 0.82, seatH = 0.42): Build {
  return (W, D, s) => {
    const arm = Math.min(0.16, W * 0.2)
    const back = Math.min(0.22, D * 0.3)
    s.box(-W, W, -D, D, 0.05, seatH, c)
    s.box(-W, W, D - back, D, seatH, backH, shade(c, 0.9))
    s.box(-W, -W + arm, -D, D - back, seatH, seatH + 0.18, shade(c, 0.95))
    s.box(W - arm, W, -D, D - back, seatH, seatH + 0.18, shade(c, 0.95))
    s.box(-W + 0.03, W - 0.03, -D + 0.03, D - 0.03, 0, 0.05, DARK)
  }
}

/** Machine body with a cabin and a glazed front (front = -y). */
function machine(bodyH: number, cabH: number, accent = MACHINE_ACCENT): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, 0, 0.08, DARK)
    s.box(-W * 0.98, W * 0.98, -D * 0.98, D * 0.98, 0.08, bodyH, MACHINE)
    s.box(-W * 0.98, W * 0.98, -D * 0.98 - 0.005, D * 0.98 + 0.005, bodyH - 0.1, bodyH - 0.02, accent)
    if (cabH > bodyH) {
      s.box(-W * 0.86, W * 0.86, -D * 0.82, D * 0.86, bodyH, cabH, MACHINE_TOP)
      // Glazing on both long faces (the data does not say which side is the front).
      s.box(-W * 0.62, W * 0.62, -D * 0.82 - 0.01, -D * 0.82, bodyH + 0.12, cabH - 0.12, GLASS)
      s.box(-W * 0.62, W * 0.62, D * 0.86, D * 0.86 + 0.01, bodyH + 0.12, cabH - 0.12, GLASS)
      s.box(-W * 0.86, W * 0.86, -D * 0.82, D * 0.86, cabH - 0.04, cabH, shade(MACHINE_TOP, 0.9))
    }
  }
}

function wallBox(z0: number, z1: number, c: string, depth = 0.25): Build {
  return (W, D, s) => s.box(-W, W, D - Math.min(2 * D, depth), D, z0, z1, c)
}

function panel(z0: number, z1: number, face: string, frame: string, stand = false): Build {
  return (W, D, s) => {
    const t = Math.min(0.06, 2 * D)
    s.box(-W, W, D - t, D, z0, z1, frame)
    s.box(-W + 0.04, W - 0.04, D - t - 0.006, D - t, z0 + 0.04, z1 - 0.04, face)
    if (stand) s.box(-0.04, 0.04, D - t, D, 0, z0, frame)
  }
}

export function shade(hex: string, f: number): string {
  const c = new THREE.Color(hex)
  c.multiplyScalar(f)
  return '#' + c.getHexString()
}

// ---------- the catalogue ----------
const tables = (h: number, top: string, leg = GREY): ModelSpec => ({ h, c: top, build: table(h, top, leg) })
const chairs = (c: string): ModelSpec => ({ h: 0.88, c, back: 'table', build: chair(c) })

export const MODELS: Record<string, ModelSpec> = {
  // --- office & public furniture ---
  desk: {
    h: 0.74, c: WOOD, back: 'wall',
    build: (W, D, s) => {
      table(0.74, WOOD)(W, D, s)
      s.box(-W + 0.06, W - 0.06, D - 0.05, D - 0.03, 0.3, 0.7, LIGHT)
      if (W >= 0.45) {
        s.box(-0.26, 0.26, D * 0.45, D * 0.45 + 0.03, 0.82, 1.12, DARK)
        s.box(-0.04, 0.04, D * 0.45 + 0.03, D * 0.45 + 0.07, 0.74, 0.84, GREY)
      }
    },
  },
  meeting_table: tables(0.74, WOOD),
  dining_table: tables(0.74, WOOD),
  seminar_table: tables(0.74, WOOD),
  canteen_table: tables(0.74, LIGHT),
  reading_table: tables(0.74, WOOD),
  outdoor_table: tables(0.72, '#b8c2cc', DARK),
  coffee_table: tables(0.45, WOOD_DARK, WOOD_DARK),
  reception_desk: { h: 1.1, c: LIGHT, build: counter(1.05, '#e6dfd2', WOOD_DARK) },
  serving_counter: { h: 1.0, c: LIGHT, build: counter(0.95, LIGHT, METAL) },
  lectern: { h: 1.15, c: WOOD, build: (W, D, s) => { s.box(-W * 0.8, W * 0.8, -D * 0.6, D * 0.8, 0, 1.05, WOOD); s.box(-W, W, -D, D, 1.05, 1.12, WOOD_DARK) } },
  chair: chairs(CHAIR),
  dining_chair: chairs(WOOD_DARK),
  outdoor_chair: chairs('#8e9aa6'),
  office_chair: {
    h: 1.0, c: DARK, back: 'table',
    build: (W, D, s) => {
      const r = Math.min(W, D) * 0.85
      s.cyl(0, 0, r, 0, 0.05, DARK, 10)
      s.cyl(0, 0, 0.03, 0.05, 0.42, GREY, 6)
      s.box(-W * 0.8, W * 0.8, -D * 0.8, D * 0.7, 0.42, 0.5, DARK)
      s.box(-W * 0.75, W * 0.75, D * 0.6, D * 0.78, 0.55, 1.0, DARK)
    },
  },
  armchair: { h: 0.8, c: FABRIC, back: 'wall', build: sofa(FABRIC, 0.8) },
  sofa: { h: 0.82, c: FABRIC, back: 'wall', build: sofa(FABRIC) },
  bench: {
    h: 0.46, c: WOOD,
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0.4, 0.46, WOOD)
      s.box(-W + 0.1, -W + 0.16, -D + 0.04, D - 0.04, 0, 0.4, GREY)
      s.box(W - 0.16, W - 0.1, -D + 0.04, D - 0.04, 0, 0.4, GREY)
    },
  },
  lecture_seating_row: {
    h: 0.9, c: CHAIR, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, -D * 0.55, 0.72, 0.76, WOOD) // writing shelf
      s.box(-W, W, -D * 0.1, D * 0.5, 0.42, 0.48, CHAIR)
      s.box(-W, W, D * 0.5, D * 0.68, 0.42, 0.9, shade(CHAIR, 0.85))
      const n = Math.max(2, Math.round(W / 0.9) + 1)
      for (let i = 0; i < n; i++) {
        const x = -W + 0.05 + ((2 * W - 0.1) * i) / (n - 1)
        s.box(x - 0.03, x + 0.03, -D, D * 0.6, 0, 0.42, GREY)
      }
    },
  },
  bookshelf: { h: 2.0, c: WOOD, back: 'wall', build: shelf(2.0, WOOD, 5, 'books') },
  shelf: { h: 1.9, c: METAL, back: 'wall', build: shelf(1.9, METAL, 4, 'books') },
  paper_shelf: { h: 1.6, c: METAL, back: 'wall', build: shelf(1.6, METAL, 4, 'books') },
  filing_cabinet: { h: 1.3, c: CABINET, back: 'wall', build: cabinet(1.3, CABINET, { drawers: 4 }) },
  shoe_cabinet: { h: 1.0, c: '#e9e1d3', back: 'wall', build: cabinet(1.0, '#e9e1d3', { seams: 1 }) },
  wardrobe: { h: 2.0, c: '#e9e1d3', back: 'wall', build: cabinet(2.0, '#e9e1d3', { seams: 1 }) },
  nightstand: { h: 0.5, c: WOOD, back: 'wall', build: cabinet(0.5, WOOD, { drawers: 2 }) },
  locker: { h: 1.9, c: '#a9bfd6', back: 'wall', build: cabinet(1.9, '#a9bfd6', { seams: 2 }) },
  tool_cabinet: { h: 1.0, c: '#c95a4e', back: 'wall', build: cabinet(1.0, '#c95a4e', { drawers: 5 }) },
  chemical_cabinet: { h: 1.9, c: YELLOW, back: 'wall', build: cabinet(1.9, YELLOW, { seams: 1 }) },
  coat_rack: {
    h: 1.75, c: WOOD_DARK, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.04, WOOD_DARK)
      s.cyl(0, 0, 0.025, 0, 1.75, WOOD_DARK, 6)
      s.box(-W * 0.9, W * 0.9, -0.03, 0.03, 1.6, 1.65, WOOD_DARK)
    },
  },
  plant: {
    h: 0.9, c: GREEN,
    build: (W, D, s) => {
      const m = Math.min(W, D)
      s.cyl(0, 0, m * 0.6, 0, 0.32, POT, 10)
      s.ball(0, 0, 0.32 + m * 0.85, m * 1.05, GREEN)
    },
  },
  bed: { h: 0.95, c: LINEN, back: 'wall', build: bed() },
  single_bed: { h: 0.95, c: LINEN, back: 'wall', build: bed() },
  tv_stand: {
    h: 1.15, c: WOOD_DARK, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.48, WOOD_DARK)
      s.box(-W * 0.8, W * 0.8, D * 0.2, D * 0.35, 0.55, 1.15, DARK)
      s.box(-0.05, 0.05, D * 0.2, D * 0.35, 0.48, 0.55, DARK)
    },
  },

  // --- kitchen & bathroom ---
  kitchen_counter: {
    h: 0.9, c: '#ece6dc', back: 'wall',
    build: (W, D, s) => {
      s.box(-W + 0.02, W - 0.02, -D + 0.05, D, 0, 0.08, DARK)
      s.box(-W, W, -D + 0.02, D, 0.08, 0.86, '#ece6dc')
      s.box(-W, W, -D, D, 0.86, 0.9, '#8d9399')
    },
  },
  stove: {
    h: 0.9, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.86, WHITE)
      s.box(-W, W, -D, D, 0.86, 0.9, DARK)
      s.box(-W * 0.7, W * 0.7, -D - 0.006, -D, 0.2, 0.62, '#5b6168')
    },
  },
  fridge: {
    h: 1.8, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 1.8, WHITE)
      s.box(-W + 0.02, W - 0.02, -D - 0.006, -D, 1.15, 1.17, METAL)
    },
  },
  dishwasher: { h: 0.86, c: WHITE, back: 'wall', build: cabinet(0.86, WHITE, { drawers: 1 }) },
  washing_machine: {
    h: 0.86, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.86, WHITE)
      s.box(-W * 0.55, W * 0.55, -D - 0.008, -D, 0.2, 0.62, '#9fb0c2')
    },
  },
  microwave: { h: 1.2, c: LIGHT, back: 'wall', build: onCounter(0.9, 0.3, LIGHT, DARK) },
  coffee_machine: { h: 1.3, c: DARK, back: 'wall', build: onCounter(0.9, 0.4, DARK, GREY) },
  sink: {
    h: 0.88, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W * 0.3, W * 0.3, 0, D, 0, 0.75, WHITE)
      s.box(-W, W, -D, D, 0.75, 0.88, WHITE)
      s.box(-W * 0.72, W * 0.72, -D * 0.65, D * 0.45, 0.881, 0.884, '#cfd8e1')
      s.box(-0.02, 0.02, D * 0.6, D * 0.8, 0.88, 1.05, METAL)
    },
  },
  toilet: {
    h: 0.8, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      const tank = Math.min(0.2, D * 0.35)
      s.box(-W * 0.9, W * 0.9, D - tank, D, 0.38, 0.8, WHITE)
      s.box(-W * 0.35, W * 0.35, -D * 0.4, D - tank, 0, 0.36, WHITE)
      s.cyl(0, -D * 0.15, Math.min(W * 0.85, D * 0.55), 0.3, 0.42, WHITE, 12)
    },
  },
  bathtub: {
    h: 0.55, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.55, WHITE)
      s.box(-W + 0.07, W - 0.07, -D + 0.07, D - 0.07, 0.551, 0.554, '#d3e3f2')
    },
  },
  shower: {
    h: 2.0, c: LIGHT, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.06, WHITE)
      s.box(-W + 0.05, W - 0.05, -D + 0.05, D - 0.05, 0.061, 0.064, '#d3e3f2')
      s.box(-0.015, 0.015, D - 0.04, D - 0.01, 0.06, 2.0, METAL)
      s.cyl(0, D - 0.15, 0.1, 1.95, 1.98, METAL, 10)
    },
  },
  hand_dryer: { h: 1.35, c: LIGHT, back: 'wall', build: wallBox(1.0, 1.3, LIGHT, 0.2) },
  defibrillator: { h: 1.4, c: '#3fa45f', back: 'wall', build: wallBox(1.0, 1.4, '#3fa45f', 0.15) },
  eyewash_station: { h: 1.2, c: '#3fa45f', back: 'wall', build: wallBox(0.9, 1.2, '#3fa45f', 0.3) },
  water_dispenser: {
    h: 1.4, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W * 0.8, W * 0.8, -D * 0.8, D * 0.8, 0, 1.0, WHITE)
      s.cyl(0, 0, Math.min(W, D) * 0.6, 1.0, 1.4, '#a8c7fa', 10)
    },
  },
  vending_machine: {
    h: 1.85, c: RED, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 1.85, '#e5e7ea')
      s.box(-W * 0.85, W * 0.5, -D - 0.008, -D, 0.6, 1.7, GLASS)
      s.box(-W, W, -D - 0.008, D, 1.72, 1.85, RED)
    },
  },
  fire_extinguisher: {
    h: 0.65, c: RED, back: 'wall',
    build: (W, D, s) => {
      const r = Math.min(0.1, Math.min(W, D) * 0.6)
      s.cyl(0, 0, r, 0, 0.58, RED, 10)
      s.cyl(0, 0, r * 0.4, 0.58, 0.66, DARK, 6)
    },
  },

  // --- screens & boards ---
  whiteboard: { h: 2.0, c: WHITE, back: 'wall', build: panel(0.85, 2.0, '#ffffff', GREY) },
  projector_screen: { h: 2.3, c: WHITE, back: 'wall', build: panel(0.9, 2.3, '#ffffff', LIGHT) },
  display_screen: { h: 1.7, c: DARK, back: 'wall', build: panel(1.0, 1.7, '#2f3337', DARK, true) },
  projector: {
    h: 2.3, c: LIGHT,
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 2.1, 2.24, LIGHT)
      s.box(-0.02, 0.02, -0.02, 0.02, 2.24, 2.4, GREY)
    },
  },

  // --- IT & electrical ---
  server_rack: {
    h: 2.0, c: '#3c4043', back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 2.0, '#3c4043')
      s.box(-W + 0.04, W - 0.04, -D - 0.006, -D, 0.1, 1.9, '#5f6670')
      for (let z = 0.3; z < 1.9; z += 0.4) s.box(-W + 0.08, -W + 0.12, -D - 0.01, -D - 0.006, z, z + 0.03, '#7cc48a')
    },
  },
  ups_unit: { h: 1.0, c: '#5f6368', back: 'wall', build: cabinet(1.0, '#5f6368', {}) },
  printer: { h: 1.0, c: LIGHT, back: 'wall', build: printer(1.0) },
  multifunction_printer: { h: 1.2, c: LIGHT, back: 'wall', build: printer(1.2) },

  // --- labs ---
  lab_bench: {
    h: 0.9, c: WHITE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W + 0.02, W - 0.02, -D + 0.05, D, 0, 0.86, '#eef0f2')
      for (let x = -W + (2 * W) / 3; x < W - 0.1; x += (2 * W) / 3)
        s.box(x - 0.006, x + 0.006, -D + 0.044, -D + 0.05, 0.1, 0.82, METAL)
      s.box(-W, W, -D, D, 0.86, 0.9, '#5f6368')
      s.box(-W, W, D - 0.08, D, 0.9, 1.5, LIGHT) // reagent shelf
    },
  },
  fume_hood: {
    h: 2.2, c: MACHINE, back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.88, '#eef0f2')
      s.box(-W, W, -D, D, 0.88, 0.92, DARK)
      s.box(-W, -W + 0.08, -D, D, 0.92, 2.2, MACHINE)
      s.box(W - 0.08, W, -D, D, 0.92, 2.2, MACHINE)
      s.box(-W, W, -D * 0.2, D, 0.92, 2.2, MACHINE)
      s.box(-W, W, -D, D, 1.9, 2.2, MACHINE_TOP)
      s.box(-W + 0.08, W - 0.08, -D + 0.02, -D + 0.04, 1.3, 1.9, GLASS)
    },
  },
  microscope: { h: 1.15, c: LIGHT, build: device(0.75, 0.4, '#3c4043', true) },
  oscilloscope_station: { h: 1.15, c: WOOD, back: 'wall', build: benchWith(0.8, (_W, D, s) => s.box(-0.25, 0.25, D * 0.2, D * 0.85, 0.8, 1.12, '#3c4043')) },
  soldering_station: { h: 1.0, c: WOOD, back: 'wall', build: benchWith(0.8, (_W, D, s) => s.box(-0.15, 0.15, D * 0.3, D * 0.8, 0.8, 0.92, '#3c4043')) },

  // --- workshop & production ---
  workbench: { h: 0.9, c: '#c8b08a', back: 'wall', build: workbench() },
  inspection_bench: { h: 1.2, c: '#c8b08a', back: 'wall', build: benchWith(0.9, (_W, D, s) => s.box(-0.2, 0.2, D * 0.2, D * 0.7, 0.9, 1.2, '#3c4043')) },
  assembly_station: {
    h: 1.9, c: '#c8b08a', back: 'wall',
    build: (W, D, s) => {
      workbench()(W, D, s)
      s.box(-W + 0.05, -W + 0.1, D - 0.1, D - 0.05, 0.9, 1.9, GREY)
      s.box(W - 0.1, W - 0.05, D - 0.1, D - 0.05, 0.9, 1.9, GREY)
      s.box(-W + 0.05, W - 0.05, D * 0.5, D - 0.05, 1.5, 1.54, METAL)
      s.box(-W + 0.05, W - 0.05, D - 0.1, D - 0.05, 1.86, 1.9, MACHINE_ACCENT)
    },
  },
  packing_station: {
    h: 1.3, c: '#c8b08a', back: 'wall',
    build: (W, D, s) => {
      workbench()(W, D, s)
      s.box(-W * 0.8, -W * 0.3, -D * 0.4, D * 0.5, 0.9, 1.25, CARGO)
      s.box(W * 0.2, W * 0.7, -D * 0.2, D * 0.6, 0.9, 1.1, shade(CARGO, 0.92))
    },
  },
  welding_station: {
    h: 1.9, c: '#c8b08a', back: 'wall',
    build: (W, D, s) => {
      table(0.85, METAL, DARK)(W * 0.7, D * 0.6, s)
      s.box(-W, W, D - 0.05, D, 0, 1.9, '#e9a64a')
      s.box(-W, -W + 0.05, -D, D, 0, 1.9, '#e9a64a')
      s.box(W * 0.75, W * 0.95, -D * 0.9, -D * 0.5, 0, 0.9, '#5f6368')
    },
  },
  pallet_rack: {
    h: 2.6, c: ORANGE, back: 'wall',
    build: (W, D, s) => {
      const t = 0.08
      const bays = Math.max(1, Math.round((2 * W) / 1.35))
      for (let i = 0; i <= bays; i++) {
        const x = -W + t / 2 + ((2 * W - t) * i) / bays
        for (const y of [-D + t / 2, D - t / 2]) s.box(x - t / 2, x + t / 2, y - t / 2, y + t / 2, 0, 2.6, BEAM)
      }
      for (const z of [0.12, 1.0, 1.85]) {
        for (const y of [-D, D - t]) s.box(-W, W, y, y + t, z, z + 0.1, ORANGE)
        for (let i = 0; i < bays; i++) {
          const x0 = -W + ((2 * W) * i) / bays + 0.12
          const x1 = -W + ((2 * W) * (i + 1)) / bays - 0.12
          s.box(x0, x1, -D + 0.1, D - 0.1, z + 0.1, z + 0.22, '#c49a6c') // pallet
          s.box(x0 + 0.04, x1 - 0.04, -D + 0.14, D - 0.14, z + 0.22, z + 0.72, (i + Math.round(z)) % 2 ? CARGO : shade(CARGO, 0.88))
        }
      }
    },
  },
  cnc_mill: { h: 2.2, c: MACHINE, build: machine(1.0, 2.2) },
  laser_cutter: { h: 1.25, c: MACHINE, build: machine(0.95, 1.25, RED) },
  curing_oven: { h: 2.1, c: METAL, build: cabinet(2.1, METAL, { seams: 1 }) },
  industrial_3d_printer: { h: 1.9, c: MACHINE, build: printer3d(1.9) },
  '3d_printer': { h: 0.7, c: DARK, build: printer3d(0.7) },
  drill_press: {
    h: 1.8, c: MACHINE_ACCENT,
    build: (W, D, s) => {
      s.box(-W * 0.7, W * 0.7, -D * 0.7, D * 0.7, 0, 0.1, DARK)
      s.cyl(0, D * 0.35, 0.06, 0.1, 1.75, METAL, 8)
      s.box(-W * 0.35, W * 0.35, -D * 0.45, D * 0.2, 0.75, 0.8, METAL)
      s.box(-W * 0.3, W * 0.3, -D * 0.5, D * 0.5, 1.4, 1.8, MACHINE_ACCENT)
    },
  },
  injection_moulding_machine: {
    h: 2.0, c: MACHINE,
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.9, MACHINE)
      s.box(-W, W, -D - 0.005, D + 0.005, 0.82, 0.9, MACHINE_ACCENT)
      s.box(-W * 0.98, -W * 0.15, -D * 0.85, D * 0.85, 0.9, 1.9, MACHINE_TOP)
      s.box(-W * 0.85, -W * 0.3, -D * 0.85 - 0.01, -D * 0.85, 1.05, 1.75, GLASS)
      s.cylX(-W * 0.15, W * 0.9, 0, 1.15, Math.min(0.25, D * 0.25), METAL)
      s.box(W * 0.45, W * 0.7, -0.22, 0.22, 1.3, 1.95, MACHINE_ACCENT)
    },
  },
  waterjet_cutter: {
    h: 1.8, c: MACHINE,
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.85, GREY)
      s.box(-W + 0.1, W - 0.1, -D + 0.1, D - 0.1, 0.85, 0.86, '#a9cdee')
      s.box(-W, -W + 0.15, -D, D, 0.85, 1.1, MACHINE)
      s.box(W - 0.15, W, -D, D, 0.85, 1.1, MACHINE)
      s.box(-W, W, -0.15, 0.15, 1.5, 1.75, MACHINE_ACCENT)
      s.box(-W, -W + 0.15, -0.15, 0.15, 1.1, 1.5, MACHINE)
      s.box(W - 0.15, W, -0.15, 0.15, 1.1, 1.5, MACHINE)
      s.box(-0.15, 0.15, -0.3, 0.1, 1.0, 1.5, DARK)
    },
  },
  coordinate_measuring_machine: {
    h: 2.0, c: '#5f6368',
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.8, '#5f6368')
      s.box(-W + 0.05, -W + 0.2, -0.1, 0.1, 0.8, 2.0, MACHINE_TOP)
      s.box(W - 0.2, W - 0.05, -0.1, 0.1, 0.8, 2.0, MACHINE_TOP)
      s.box(-W + 0.05, W - 0.05, -0.12, 0.12, 1.85, 2.0, MACHINE_TOP)
      s.box(-0.08, 0.08, -0.08, 0.08, 1.2, 1.85, MACHINE_ACCENT)
    },
  },
  compressor: {
    h: 1.2, c: MACHINE,
    build: (W, D, s) => {
      s.box(-W, W, -D, D, 0, 0.1, DARK)
      s.cylX(-W * 0.95, W * 0.95, -D * 0.3, 0.45, Math.min(0.35, D * 0.6), MACHINE_ACCENT)
      s.box(-W * 0.6, W * 0.3, D * 0.1, D * 0.95, 0.1, 1.2, MACHINE)
    },
  },
  conveyor: {
    h: 0.9, c: DARK,
    build: (W, D, s) => {
      const n = Math.max(2, Math.round(W / 0.8) + 1)
      for (let i = 0; i < n; i++) {
        const x = -W + 0.05 + ((2 * W - 0.1) * i) / (n - 1)
        for (const y of [-D + 0.05, D - 0.05]) s.box(x - 0.03, x + 0.03, y - 0.03, y + 0.03, 0, 0.78, GREY)
      }
      s.box(-W, W, -D, D, 0.78, 0.86, '#3c4043')
      s.box(-W, W, -D - 0.02, -D + 0.04, 0.78, 0.92, METAL)
      s.box(-W, W, D - 0.04, D + 0.02, 0.78, 0.92, METAL)
    },
  },
  forklift: {
    h: 2.2, c: YELLOW,
    build: (W, D, s) => {
      // forks at -y, counterweight at +y
      const wr = Math.min(0.24, W * 0.4)
      s.box(-W * 0.85, W * 0.85, -D * 0.3, D, 0.2, 1.05, YELLOW)
      s.box(-W * 0.85, W * 0.85, D * 0.65, D, 0.2, 1.25, DARK)
      for (const sy of [-1, 1]) for (const sx of [-1, 1])
        s.box(sx * W * 0.8 - 0.03, sx * W * 0.8 + 0.03, sy > 0 ? D * 0.55 : -D * 0.25, (sy > 0 ? D * 0.55 : -D * 0.25) + 0.06, 1.05, 2.1, DARK)
      s.box(-W * 0.85, W * 0.85, -D * 0.25, D * 0.61, 2.05, 2.12, DARK)
      s.box(-W * 0.4, W * 0.4, D * 0.1, D * 0.5, 1.05, 1.35, DARK) // seat
      s.box(-W * 0.75, -W * 0.6, -D * 0.42, -D * 0.32, 0.1, 2.3, GREY) // mast
      s.box(W * 0.6, W * 0.75, -D * 0.42, -D * 0.32, 0.1, 2.3, GREY)
      s.box(-W * 0.6, -W * 0.35, -D, -D * 0.42, 0.06, 0.12, GREY) // forks
      s.box(W * 0.35, W * 0.6, -D, -D * 0.42, 0.06, 0.12, GREY)
      for (const sx of [-1, 1]) {
        s.cylX(sx * W * 0.85 - 0.08, sx * W * 0.85 + 0.08, -D * 0.15, wr, wr, '#2b2f33')
        s.cylX(sx * W * 0.85 - 0.08, sx * W * 0.85 + 0.08, D * 0.7, wr, wr, '#2b2f33')
      }
    },
  },
  pallet_jack: {
    h: 1.1, c: YELLOW,
    build: (W, D, s) => {
      // forks along the depth axis is wrong for 1.6 x 0.7 footprints: forks run along x (the long side)
      s.box(-W, W * 0.6, -D * 0.9, -D * 0.25, 0.04, 0.12, YELLOW)
      s.box(-W, W * 0.6, D * 0.25, D * 0.9, 0.04, 0.12, YELLOW)
      s.box(W * 0.6, W * 0.85, -D * 0.9, D * 0.9, 0.04, 0.35, YELLOW)
      s.box(W * 0.85, W * 0.9, -0.03, 0.03, 0.35, 1.1, DARK)
    },
  },
  forklift_charging_station: {
    h: 1.4, c: '#5f6368', back: 'wall',
    build: (W, D, s) => {
      s.box(-W, W, 0, D, 0, 1.4, '#5f6368')
      s.box(-W * 0.6, -W * 0.3, -0.008, 0, 1.0, 1.2, '#7cc48a')
    },
  },
}

function counter(h: number, body: string, top: string): Build {
  return (W, D, s) => {
    s.box(-W, W, -D * 0.6, D, 0, h, body)
    s.box(-W, W, -D, D, h, h + 0.05, top)
  }
}

function onCounter(z0: number, hh: number, body: string, face: string): Build {
  return (W, D, s) => {
    s.box(-W * 0.9, W * 0.9, -D * 0.8, D * 0.9, z0, z0 + hh, body)
    s.box(-W * 0.7, W * 0.3, -D * 0.8 - 0.006, -D * 0.8, z0 + hh * 0.2, z0 + hh * 0.8, face)
  }
}

function bed(): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, 0, 0.28, WOOD)
    s.box(-W + 0.03, W - 0.03, -D + 0.03, D - 0.06, 0.28, 0.48, LINEN)
    s.box(-W + 0.04, W - 0.04, -D + 0.02, D * 0.35, 0.48, 0.53, DUVET)
    s.box(-W * 0.8, W * 0.8, D - 0.5, D - 0.12, 0.48, 0.58, '#eef1f4')
    s.box(-W, W, D - 0.06, D, 0, 0.95, WOOD_DARK)
  }
}

function printer(h: number): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, 0, h - 0.15, LIGHT)
    s.box(-W, W, -D * 0.7, D, h - 0.15, h, '#c7ccd2')
    s.box(-W * 0.6, W * 0.6, -D - 0.006, -D, h - 0.4, h - 0.3, DARK)
  }
}

function printer3d(h: number): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, 0, 0.14, DARK)
    s.box(-W + 0.03, W - 0.03, -D + 0.03, D - 0.03, 0.14, h - 0.12, '#c9d6e3')
    s.box(-W, W, -D, D, h - 0.12, h, h > 1 ? MACHINE_TOP : DARK)
    s.box(-W + 0.06, W - 0.06, -D + 0.06, D - 0.06, 0.14, 0.18, MACHINE_ACCENT)
  }
}

function workbench(): Build {
  return (W, D, s) => {
    s.box(-W, W, -D, D, 0.85, 0.9, '#c8b08a')
    legs(s, W - 0.03, D - 0.03, 0.06, 0, 0.85, GREY)
    s.box(-W + 0.05, W - 0.05, -D + 0.05, D - 0.05, 0.15, 0.18, METAL)
  }
}

function benchWith(h: number, extra: Build): Build {
  return (W, D, s) => {
    table(h, WOOD)(W, D, s)
    extra(W, D, s)
  }
}

/** A small device on its own stand (assets whose footprint is the device itself, like microscopes). */
function device(standH: number, hh: number, c: string, withStand: boolean): Build {
  return (W, D, s) => {
    if (withStand) table(standH, LIGHT)(W, D, s)
    s.box(-W * 0.35, W * 0.35, -D * 0.35, D * 0.35, standH, standH + 0.04, c)
    s.box(-W * 0.12, W * 0.12, 0, D * 0.3, standH + 0.04, standH + hh, c)
    s.box(-W * 0.1, W * 0.1, -D * 0.25, D * 0.1, standH + hh * 0.55, standH + hh * 0.85, GREY)
  }
}

/** Spec for any asset: its typed model, else a plain box styled by category. */
export function specOf(type: string, category: string): ModelSpec {
  const hit = MODELS[type]
  if (hit) return hit
  const st = CATEGORY_STYLE[category] ?? DEFAULT_STYLE
  return { h: st.h, c: st.color, build: (W, D, s) => s.box(-W, W, -D, D, 0, st.h, st.color) }
}

// ---------- geometry ----------

const tmpColor = new THREE.Color()

/** Drop uvs and paint a primitive in one colour (vertex colours), ready for mergeGeometries. */
export function finish(g: THREE.BufferGeometry, c: string): THREE.BufferGeometry {
  g.deleteAttribute('uv')
  tmpColor.set(c)
  const n = g.getAttribute('position').count
  const col = new Float32Array(n * 3)
  for (let i = 0; i < n; i++) {
    col[i * 3] = tmpColor.r
    col[i * 3 + 1] = tmpColor.g
    col[i * 3 + 2] = tmpColor.b
  }
  g.setAttribute('color', new THREE.BufferAttribute(col, 3))
  return g
}

/** Merged geometry of one model at one footprint size, in three's frame (x, z up -> y, scene y -> -z). */
export function buildModelGeometry(spec: ModelSpec, w: number, d: number): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = []
  const sink: Sink = {
    box(x0, x1, y0, y1, z0, z1, c) {
      const sx = Math.abs(x1 - x0)
      const sy = Math.abs(y1 - y0)
      const sz = Math.abs(z1 - z0)
      if (sx < 1e-4 || sy < 1e-4 || sz < 1e-4) return
      const g = new THREE.BoxGeometry(sx, sz, sy)
      g.translate((x0 + x1) / 2, (z0 + z1) / 2, -(y0 + y1) / 2)
      parts.push(finish(g, c))
    },
    cyl(cx, cy, r, z0, z1, c, seg = 8) {
      if (r <= 0 || z1 <= z0) return
      const g = new THREE.CylinderGeometry(r, r, z1 - z0, seg, 1)
      g.translate(cx, (z0 + z1) / 2, -cy)
      parts.push(finish(g, c))
    },
    cylX(x0, x1, cy, cz, r, c) {
      if (r <= 0 || x1 <= x0) return
      const g = new THREE.CylinderGeometry(r, r, x1 - x0, 10, 1)
      g.rotateZ(Math.PI / 2)
      g.translate((x0 + x1) / 2, cz, -cy)
      parts.push(finish(g, c))
    },
    ball(cx, cy, cz, r, c) {
      if (r <= 0) return
      const g = new THREE.SphereGeometry(r, 9, 6)
      g.translate(cx, cz, -cy)
      parts.push(finish(g, c))
    },
  }
  spec.build(w / 2, d / 2, sink)
  if (!parts.length) sink.box(-w / 2, w / 2, -d / 2, d / 2, 0, spec.h, spec.c)
  const merged = mergeGeometries(parts, false)
  for (const p of parts) p.dispose()
  merged.computeBoundingSphere()
  merged.computeBoundingBox()
  return merged
}
