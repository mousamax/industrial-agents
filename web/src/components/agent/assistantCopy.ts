// What the assistant says while it works and how an answer is shaped on screen. People see what the guide is
// doing in plain words ("Finding a way without stairs…"), never tool names, and every answer leads with one
// sentence; the agent's longer text is trimmed to at most one supporting sentence.
import { fmtM, fmtWalk, type ScenarioResult, type TraceItem } from '../../lib/buildingGraph'

/** Plain-language lines per tool. Two per tool so repeated questions don't feel canned; picked by args. */
const STATUS: Record<string, [string, string]> = {
  find: ['Looking around the building…', 'Searching every floor for you…'],
  room_details: ['Taking a closer look at that room…', 'Checking what’s inside…'],
  list_rooms: ['Walking through the rooms…', 'Measuring the rooms for you…'],
  count_items: ['Counting them up…', 'Doing a quick headcount of the equipment…'],
  distance_3d: ['Measuring straight through the floors…', 'Measuring as the crow flies…'],
  route: ['Finding the easiest way there…', 'Planning your route…'],
  walk_directions: ['Working out each turn for you…', 'Writing down the way, step by step…'],
  nearest: ['Checking what’s closest to you…', 'Finding the nearest one…'],
  reachable_rooms: ['Checking where you can get to…', 'Walking every corridor for you…'],
  what_if: ['Trying the change out on the map…', 'Playing it through, nothing changes yet…'],
  calculate: ['Doing the maths…', 'Crunching the numbers…'],
  set_closed: ['Updating the map for everyone…', 'Putting up the barrier…'],
  add_door: ['Sketching the new door…', 'Fitting the new door…'],
  move_item: ['Moving it on the map…', 'Finding it a new spot…'],
  item_history: ['Looking back through the history…', 'Checking where it has been…'],
  building_changes: ['Catching up on what changed…', 'Reading the building’s diary…'],
  undo_last_change: ['Putting it back the way it was…', 'Undoing that change…'],
  report_item: ['Noting that down…', 'Updating the map…'],
}

const STEP_FREE_ROUTE = 'Finding a way without stairs…'
const WIDE_ROUTE = 'Finding a route wide enough for you…'

function pick(pair: [string, string], seed: string): string {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0
  return pair[Math.abs(h) % 2]
}

/** One friendly line for what the agent is doing right now (or just did). */
export function workingLine(trace: TraceItem[]): string {
  const cur = [...trace].reverse().find((t) => t.running) ?? trace[trace.length - 1]
  if (!cur) return 'Thinking about that…'
  if (cur.tool === 'route' || cur.tool === 'walk_directions' || cur.tool === 'nearest') {
    if (/min_width|wide|≥/i.test(cur.args)) return WIDE_ROUTE
    if (/step.?free/i.test(cur.args)) return STEP_FREE_ROUTE
  }
  const pair = STATUS[cur.tool]
  return pair ? pick(pair, cur.args) : 'Working on it…'
}

export interface ShapedAnswer {
  /** One sentence: the answer itself. Spoken first. */
  headline: string
  /** At most one supporting sentence, or empty. */
  detail: string
  /** Facts from the map itself (distance, time), so they always agree with the drawn route. */
  meta: string
}

const SENTENCE = /(?<=[.!?])\s+(?=[A-Z0-9"“(])/

function clean(text: string): string {
  return text
    .replace(/\*\*/g, '')
    .replace(/^#+\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Cut an inline numbered list ("… 1. Leave the lobby 2. Turn left …"); steps are shown on their own. */
function withoutInlineSteps(text: string): string {
  // A list has a second step in the same style, and its "1." doesn't follow a word: "(Floor 1) to …" and
  // "… on Floor 1. Answer: …" are not lists.
  const m = text.search(/(^|\s)(?<![A-Za-z]\s)1([.)])\s(?=.*\s2\2\s)/)
  return m > 0 ? text.slice(0, m).trim() : text
}

/** Drop note-like fragments the model sometimes leaks ("Several zones; use nearest to see."). */
function isNoteFragment(s: string): boolean {
  return s.length < 70 && /;/.test(s) && !/\b(is|are|was|will|can|you|I)\b/i.test(s)
}

/** Drop the model thinking out loud ("Need to check both floors, including halls probably."). */
function isThinkingAloud(s: string): boolean {
  return /^(need to|let me|let's check|i('ll| will| need| should| must)|first,? (i|let)|now (i|let)|checking|looking at|probably)\b/i.test(
    s,
  )
}

export function shapeAnswer(result: ScenarioResult): ShapedAnswer {
  const body = withoutInlineSteps(clean(result.understood))
  const sentences = body
    .split(SENTENCE)
    .map((s) => s.trim())
    .filter((s) => s && !isNoteFragment(s) && !isThinkingAloud(s) && !/^answer:/i.test(s))
  // "I made two changes:" followed by a list is a headline of its own, not the start of a long sentence.
  const firstLine = clean(result.understood.split('\n')[0] ?? '')
  const headline = /:$/.test(firstLine) ? firstLine : (sentences[0] ?? result.title)
  const len = result.route?.length
  const meta = result.kind === 'route' && len ? `${fmtM(len)} · about ${fmtWalk(len)}` : ''
  // A supporting sentence that only repeats the distance already shown in `meta` adds nothing.
  const restatesDistance = (s: string) =>
    !!len &&
    [...s.matchAll(/(\d+(?:[.,]\d+)?)\s*(?:m|metres|meters)\b/gi)].some(
      (m) => Math.abs(Number(m[1].replace(',', '.')) - len) < 1,
    )
  // When the guide ends on a question (what a drafted plan didn't say, which room you meant), the question is
  // what the person must read: show it with the sentence that sets it up.
  const ask = sentences.findIndex((s, i) => i > 0 && s.endsWith('?'))
  const detail =
    ask > 0
      ? sentences.slice(ask > 1 && !sentences[ask - 1].endsWith('?') ? ask - 1 : ask, ask + 1).join(' ')
      : (sentences.slice(1).find((s) => !restatesDistance(s)) ?? '')
  return { headline, detail, meta }
}

export interface ReplyBlock {
  kind: 'p' | 'li'
  text: string
}

/** The whole reply after its headline, as paragraphs and list items, so nothing the guide said is cut. */
export function replyBlocks(text: string, headline: string): ReplyBlock[] {
  const blocks: ReplyBlock[] = []
  let inTable = false
  for (const raw of text.replace(/\*\*/g, '').split('\n')) {
    const line = raw.replace(/^#+\s*/, '').trim()
    if (!line || /^answer:/i.test(line)) continue
    // A markdown table reads as a list: its header and the |---| rule are dropped, each row is one line.
    if (line.startsWith('|')) {
      const cells = line.split('|').map((c) => c.trim()).filter(Boolean)
      const rule = cells.every((c) => /^:?-+:?$/.test(c))
      if (inTable && !rule) blocks.push({ kind: 'li', text: cells.join(cells.length === 2 ? ': ' : ', ') })
      inTable = true
      continue
    }
    inTable = false
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/)
    blocks.push(item ? { kind: 'li', text: item[1] } : { kind: 'p', text: line })
  }
  const first = blocks[0]
  if (first && clean(first.text).startsWith(headline)) {
    const rest = clean(first.text).slice(headline.length).trim()
    if (rest) first.text = rest
    else blocks.shift()
  }
  return blocks.filter((b) => !isThinkingAloud(b.text))
}

/** An earlier reply in the thread, as one plain paragraph. */
export function plainReply(text: string): string {
  return replyBlocks(text, '')
    .map((b) => b.text)
    .join(' ')
}

/** A short label to pin on the map next to the answer room, or null when the headline is too long for it. */
export function mapLabel(result: ScenarioResult, headline: string): string | null {
  if (result.kind !== 'impact' || (result.focusIds?.length ?? 0) !== 1) return null
  const area = headline.match(/(\d+(?:[.,]\d+)?)\s*m²/)
  if (area) return `${result.title}, ${area[1]} m²`
  return headline.length <= 44 ? headline.replace(/\.$/, '') : result.title
}

/** What a person reads when the assistant can't be reached: what happened and what to do, no raw errors. */
export const OFFLINE_HEADLINE = 'I can’t reach the building right now.'
export const OFFLINE_DETAIL = 'Check your connection and try again in a moment.'
