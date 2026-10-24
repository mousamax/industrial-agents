import type { BuildingInfo, Scene } from './types'

export const API = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`)
  return res.json() as Promise<T>
}

export const fetchBuildings = () => get<BuildingInfo[]>('/buildings')
export const fetchScene = (id: string) => get<Scene>(`/buildings/${encodeURIComponent(id)}/scene`)
export const chatUrl = (id: string) => `${API}/buildings/${encodeURIComponent(id)}/chat`

export interface Drafted {
  building_id: string
  name: string
  floors: number
  rooms: number
  doors: number
  stairs_and_lifts: number
  items: number
  open_questions: number
  seconds: number
}

/** Level 4: floor plans (floor_<level>.svg / .png, one per floor) -> a new building the guide then reviews with you. */
export async function uploadPlans(files: File[], name: string): Promise<Drafted> {
  const form = new FormData()
  for (const f of files) form.append('plans', f, f.name)
  form.append('name', name)
  const res = await fetch(`${API}/buildings/draft`, { method: 'POST', body: form })
  if (!res.ok) {
    const detail = await res.json().then((b: { detail?: unknown }) => b.detail).catch(() => null)
    throw new Error(typeof detail === 'string' ? detail : `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<Drafted>
}
