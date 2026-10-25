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

// ---- room calendar (demo data in the shape of Outlook/Google room resources; app/api/main.py) ----

export interface CalendarEvent {
  event: string
  room: string
  room_id: string
  floor: string
  date: string
  weekday: string
  from?: string
  until?: string
  when?: string
  status: string
  organizer: string
  about: string
}

export interface BookingRequest {
  id: string
  title: string
  from: string
  received: string
  weekday: string
  date: string
  start: string
  end: string
  people: number
  teams?: number
  needs: string[]
  message: string
}

export interface BuildingCalendar {
  connected: boolean
  today: string
  now: string
  rooms: Record<string, { room: string; floor_id: string; seats: number; equipment: string[] }>
  events: CalendarEvent[]
  requests: BookingRequest[]
}

export interface PlanRow {
  role: 'main' | 'breakout' | 'catering' | 'kitchen' | 'toilets'
  room_id: string
  room: string
  floor: string
  why: string
  seats?: number
  teams?: number
  walk_m?: number
}

export interface EventPlan {
  request: {
    title: string | null
    date: string
    weekday: string
    from: string
    until: string
    people: number
    teams?: number
    needs?: string[]
  }
  plan: PlanRow[]
  checked?: string[]
  avoided: { room: string; room_id: string; why: string }[]
  note?: string
  highlight?: string[]
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`)
  return res.json() as Promise<T>
}

const cal = (id: string) => `/buildings/${encodeURIComponent(id)}/calendar`
export const fetchCalendar = (id: string) => get<BuildingCalendar>(cal(id))
export const planRequest = (id: string, requestId: string) => post<EventPlan>(`${cal(id)}/plan`, { request_id: requestId })
export const bookPlan = (id: string, plan: EventPlan) => post<{ booked: number }>(`${cal(id)}/book`, plan)
