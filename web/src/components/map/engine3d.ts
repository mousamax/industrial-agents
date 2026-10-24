// Lazy loader for the three.js engine: a dynamic import, so three.js is its own chunk and phones that never tilt
// never download it. Any failure (chunk missing, no WebGL) rejects and the map simply stays 2D.
import type { Map3DEngine } from '../map3d/types'

type EngineModule = { createMap3D: (canvas: HTMLCanvasElement) => Map3DEngine }

// import.meta.glob instead of import(): resolves to nothing (instead of breaking the build) while the engine
// file does not exist yet, and still code-splits it once it does.
const modules = import.meta.glob<EngineModule>('../map3d/Map3D.ts')

export async function createEngine(canvas: HTMLCanvasElement): Promise<Map3DEngine> {
  const load = modules['../map3d/Map3D.ts']
  if (!load) throw new Error('3D engine not available')
  const { createMap3D } = await load()
  return createMap3D(canvas)
}
