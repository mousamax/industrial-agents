// Colours and materials of the 3D map. Hex values mirror the 2D palette tokens in src/index.css so the
// top-down 3D view matches the SVG map; slabs and walls are unlit (colours baked), assets use soft lights.
import * as THREE from 'three'
import type { Tone } from '../map/geometry'

export const BG = '#f3f2f8' // --color-map-bg
export const ACCENT = '#ff8a3d' // --color-accent
export const ROUTE = '#ff8a3d' // --color-route
export const ROUTE_CASING = '#ff6b5a' // --color-route-casing

/** Slab fill per tone (index.css --color-map-*; outdoor matches TONE_FILL in map/geometry.ts). */
export const TONE_HEX: Record<Tone, string> = {
  room: '#ffffff',
  corridor: '#f8f7fc',
  hall: '#fff1e6',
  service: '#efedf6',
  outdoor: '#e8f3ea',
}

export const WALL_TOP = '#c9c4da' // a touch darker than --color-map-wall so 1-2 px tops read as the 2D outline
export const WALL_SIDE = '#ebe9f2'
export const SLAB_SIDE = '#d6d2e3'

/** Lower floors are greyed out like the 2D map: flat neutral slabs and outlines, no tone colours. */
export const LOWER_SLAB = '#e6e4ee'
export const LOWER_WALL = '#d8d5e3'
/** Each further floor down fades this much more toward the background (nearer floors read darker). */
export const LOWER_FADE_STEP = 0.22

const BG_LINEAR = new THREE.Color(BG)

/**
 * Unlit material with two extras injected into three's basic shader:
 * - uDim mixes the final colour toward the map background (deeper lower floors);
 * - optional `aOff` attribute (xz offset direction) scaled by uHalfT, so wall thickness can follow the zoom
 *   (walls stay ~1 px outlines when zoomed out, like the 2D map) without rebuilding geometry.
 * Vertex-coloured by default; pass `solid` for a flat colour that ignores the baked colours (lower floors).
 */
export function flatMaterial(dim: number, withOffset: boolean, solid?: string): THREE.MeshBasicMaterial {
  const mat = solid ? new THREE.MeshBasicMaterial({ color: solid }) : new THREE.MeshBasicMaterial({ vertexColors: true })
  const uniforms = { uDim: { value: dim }, uFade: { value: BG_LINEAR }, uHalfT: { value: 0.06 } }
  mat.userData.uniforms = uniforms
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms)
    if (withOffset) {
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nattribute vec2 aOff;\nuniform float uHalfT;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\ntransformed.xz += aOff * uHalfT;')
    }
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform float uDim;\nuniform vec3 uFade;')
      .replace('#include <opaque_fragment>', '#include <opaque_fragment>\ngl_FragColor.rgb = mix(gl_FragColor.rgb, uFade, uDim);')
  }
  mat.customProgramCacheKey = () => (withOffset ? 'flat-off' : 'flat')
  return mat
}

/** Footprint height and colour per asset category when a type has no model (detail 1 and fallbacks). */
export const CATEGORY_STYLE: Record<string, { h: number; color: string }> = {
  machine: { h: 1.6, color: '#c9ced6' },
  equipment: { h: 1.1, color: '#d5d9df' },
  furniture: { h: 0.75, color: '#dcd3c4' },
  fixture: { h: 0.85, color: '#f1f3f4' },
}
export const DEFAULT_STYLE = { h: 0.8, color: '#d9dce1' }
