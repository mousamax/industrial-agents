// Show mode (/show or ?show): the stage demo, hard-coded so it runs the same every time and never waits on a
// model. MUCL is selected, a pulsing mic waits for a tap, then the prompt types itself, the guide's answer
// (a real one, captured once from the live building_guide) appears, the route draws in and "Let's go" pops up.
import type { UIMessage } from 'ai'
import type { ShowRoute } from '../api/types'

const url = new URL(window.location.href)
export const SHOW = url.pathname.replace(/\/+$/, '') === '/show' || url.searchParams.has('show')

export const SHOW_BUILDING = 'munich-urban-colab'
export const SHOW_PROMPT = 'Where is the Hackathon'

/** Typing the prompt into the bar, the guide "thinking", the camera settling, and the route drawing in: the line
 *  walks at `walkMps` (map metres per second), every floor stays on screen at least `minFloorMs` (the stairs
 *  floors too, where the line barely moves), and each floor change takes `hopMs`. */
export const SHOW_TIMING = {
  typeMsPerChar: 28,
  beforeSendMs: 180,
  workingMs: 750,
  settleMs: 600,
  walkMps: 19,
  minFloorMs: 700,
  hopMs: 560,
  /** Phone: the camera follows the head of the line, keeping this many metres around it in view. */
  followRadiusM: 4.5,
  /** How long the map stays on the destination's floor before it goes back to the entrance. */
  endHoldMs: 800,
}

/** Metres a floor change counts for while the route draws in (the stairs between two floors). */
const FLOOR_CHANGE_M: number = 8

function routeParts(route: ShowRoute) {
  const segLen = route.segments.map((s) =>
    s.points.slice(1).reduce((acc, p, i) => acc + Math.hypot(p[0] - s.points[i][0], p[1] - s.points[i][1]), 0),
  )
  const hops = route.segments.map((s, i) => (i > 0 && route.segments[i - 1].floor_id !== s.floor_id ? FLOOR_CHANGE_M : 0))
  const total = segLen.reduce((a, b) => a + b, 0) + hops.reduce((a, b) => a + b, 0)
  return { segLen, hops, total }
}

/** How far the route is drawn (0..1, for truncateRoute) `ms` after it starts, and how long the whole draw takes. */
export function revealTimeline(route: ShowRoute): { totalMs: number; at: (ms: number) => number } {
  const { segLen, hops, total } = routeParts(route)
  const T = SHOW_TIMING
  const keys: [number, number][] = [[0, 0]] // (ms, metres drawn)
  let ms = 0
  let m = 0
  let si = 0
  while (si < route.segments.length) {
    if (hops[si]) keys.push([(ms += T.hopMs), (m += hops[si])])
    // One floor: this segment and any that follow on the same floor.
    let len = segLen[si]
    while (si + 1 < route.segments.length && !hops[si + 1]) len += segLen[++si]
    keys.push([(ms += Math.max(T.minFloorMs, (len / T.walkMps) * 1000)), (m += len)])
    si++
  }
  return {
    totalMs: ms,
    at: (t) => {
      if (!total || t >= ms) return 1
      const i = keys.findIndex(([k]) => k > t)
      const [t0, m0] = keys[i - 1]
      const [t1, m1] = keys[i]
      return (m0 + ((t - t0) / (t1 - t0)) * (m1 - m0)) / total
    },
  }
}

/** The first `frac` of a route by walking length; the floor changes count FLOOR_CHANGE_M each. A floor change
 *  shows once the draw is past its middle (the 3D link is drawn whole, end to end). */
export function truncateRoute(route: ShowRoute, frac: number): ShowRoute {
  if (frac >= 1) return route
  const { hops, total } = routeParts(route)
  let left = total * Math.max(0, frac)
  const segments: ShowRoute['segments'] = []
  for (let si = 0; si < route.segments.length; si++) {
    if (hops[si]) {
      if (left < hops[si] / 2) break
      left = Math.max(0, left - hops[si])
    } else if (si > 0 && left <= 0) break
    const pts = route.segments[si].points
    if (!pts.length) continue
    const out: ShowRoute['segments'][number]['points'] = [pts[0]]
    for (let i = 1; i < pts.length && left > 0; i++) {
      const [a, b] = [pts[i - 1], pts[i]]
      const d = Math.hypot(b[0] - a[0], b[1] - a[1])
      if (d <= left) {
        out.push(b)
        left -= d
      } else {
        const t = left / d
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
        left = 0
      }
    }
    segments.push({ floor_id: route.segments[si].floor_id, points: out })
  }
  return { ...route, segments }
}

/** The guide's answer to "Hi, I'm a guest. Where is the hackathon?" in MUCL, as the live backend sent it. */
export const SHOW_TURN: UIMessage = {
  id: 'show-hackathon',
  role: 'assistant',
  parts: [
    { type: 'step-start' },
    {
      type: 'tool-calendar',
      toolCallId: 'show-calendar',
      state: 'output-available',
      input: { query: 'hackathon' },
      output: JSON.stringify({
        today: '2026-10-25',
        now: '10:00',
        events: [
          {
            event: 'Industrial Agents Hackathon: Talk to Your Building',
            room: 'Offener Bürobereich Süd OPEN-S (Floor 3 (3. Obergeschoss))',
            room_id: 'F3-OPEN-S',
            floor: 'Floor 3 (3. Obergeschoss)',
            when: 'all day',
            status: 'on now',
            organizer: 'UnternehmerTUM with Synera',
            about: 'Teams build AI agents for industrial partners. Guests and visitors welcome, check in at the reception.',
            open_to_visitors: true,
          },
        ],
        highlight: ['F3-OPEN-S'],
      }),
    },
    { type: 'step-start' },
    {
      type: 'tool-route',
      toolCallId: 'show-route',
      state: 'output-available',
      input: { destination: 'F3-OPEN-S', start: 'F0-EING', via: ['F0-STAIR-C', 'F3-STAIR-C'] },
      output: JSON.stringify({
        reachable: true,
        total_length_m: 62.2,
        stops: [
          'Eingangsbereich (Ground floor (Erdgeschoss))',
          'Haupttreppe STAIR-C (Ground floor (Erdgeschoss))',
          'Haupttreppe STAIR-C (Floor 3 (3. Obergeschoss))',
          'Offener Bürobereich Süd OPEN-S (Floor 3 (3. Obergeschoss))',
        ],
        legs: [
          {
            length_m: 14.89,
            steps: [
              'start in Eingangsbereich (Ground floor (Erdgeschoss))',
              'through the opening (6 m wide) into Flur / Halle EG CORR (Ground floor (Erdgeschoss))',
              'through the opening (4 m wide) into Haupttreppe STAIR-C (Ground floor (Erdgeschoss))',
            ],
            space_path: ['F0-EING', 'F0-CORR', 'F0-STAIR-C'],
            element_path: ['O026', 'O025'],
          },
          {
            length_m: 22.0,
            steps: [
              'start in Haupttreppe STAIR-C (Ground floor (Erdgeschoss))',
              'take Haupttreppe (stairs) to Haupttreppe STAIR-C (Floor 1 (1. Obergeschoss))',
            ],
            space_path: ['F0-STAIR-C', 'F1-STAIR-C', 'F2-STAIR-C', 'F3-STAIR-C'],
            element_path: ['V03'],
          },
          {
            length_m: 25.31,
            steps: [
              'start in Haupttreppe STAIR-C (Floor 3 (3. Obergeschoss))',
              'through the opening (6 m wide) into Flur 3. OG CORR (Floor 3 (3. Obergeschoss))',
              'through the opening (2 m wide) into Offener Bürobereich Süd OPEN-S (Floor 3 (3. Obergeschoss))',
            ],
            space_path: ['F3-STAIR-C', 'F3-CORR', 'F3-OPEN-S'],
            element_path: ['O164', 'O149'],
          },
        ],
        closed_avoided: [],
        highlight: ['F0-EING', 'F0-STAIR-C', 'F3-STAIR-C', 'F3-OPEN-S'],
      }),
    },
    { type: 'step-start' },
    {
      type: 'text',
      state: 'done',
      text:
        'The hackathon is the "Industrial Agents Hackathon: Talk to Your Building" (UnternehmerTUM with Synera). ' +
        "It's on now and runs all day. Guests are welcome. It's in the Offener Bürobereich Süd on Floor 3.\n\n" +
        "I've started you at the entrance, the Eingangsbereich.\n\n" +
        '1. Walk from the Eingangsbereich through the hall to the Haupttreppe (main stairs).\n' +
        '2. Climb the Haupttreppe to Floor 3 (3. Obergeschoss).\n' +
        '3. Leave the stairwell into the Flur 3. OG corridor, then go through the opening into the Offener Bürobereich Süd.\n\n' +
        'The walk is about 62 m in total. The event notes ask guests to check in at reception.',
    },
  ],
}
