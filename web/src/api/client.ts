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
