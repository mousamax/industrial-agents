// The answer cards the guide put on screen during one conversation, so the voice agent can bring an earlier one
// back ("show me the doors again", "the route to the canteen") with its `show_on_screen` tool.
import type { ScenarioResult } from '../../lib/buildingGraph'

export interface ShownCard {
  result: ScenarioResult
  /** The change on the card is already applied (no yes needed). */
  applied: boolean
}

const keyOf = (r: ScenarioResult) => `${r.kind}|${r.title}|${r.sub}`

/** The list with `result` added as the newest card; the same card shown again (a proposal, then applied) replaces it. */
export function rememberCard(cards: ShownCard[], result: ScenarioResult, applied: boolean): ShownCard[] {
  const key = keyOf(result)
  return [...cards.filter((c) => keyOf(c.result) !== key), { result, applied }].slice(-20)
}

/** How the voice agent hears a card: its title and, for a change, whether it still waits for a yes. */
export function cardLabel(card: ShownCard): string {
  const r = card.result
  const state = r.confirm ? (card.applied ? ' (applied)' : ' (preview, waiting for a yes)') : ''
  return `${r.title}${r.sub ? ` · ${r.sub}` : ''}${state}`
}

const STOP = new Set(['the', 'and', 'show', 'again', 'please', 'that', 'this', 'one', 'with', 'from', 'for', 'me', 'to'])
const words = (text: string) =>
  text
    .toLowerCase()
    .split(/[^\p{L}\p{N}.]+/u)
    .filter((w) => w.length > 1 && !STOP.has(w))

/** The card `what` asks for, newest first among equals: by its words, or by kind ("the route", "the doors",
 * "the change waiting for a yes"); "the last one" / "previous" is the newest card that isn't on screen. */
export function pickCard(cards: ShownCard[], what: string, onScreen: ScenarioResult | null): ShownCard | null {
  const asked = words(what)
  const wantsRoute = /route|way|walk|path|direction/i.test(what)
  const wantsChange = /door|change|edit|preview|confirm|pending|waiting|clos/i.test(what)
  let best: ShownCard | null = null
  let bestScore = 0
  for (const card of cards) {
    const r = card.result
    const text = new Set(words(`${r.title} ${r.sub} ${r.understood}`))
    let score = asked.filter((w) => text.has(w)).length
    if (wantsRoute && r.kind === 'route') score += 2
    if (wantsChange && r.confirm) score += card.applied ? 2 : 3
    if (score > 0 && score >= bestScore) {
      best = card
      bestScore = score
    }
  }
  if (best) return best
  if (/last|previous|before|back|earlier/i.test(what)) {
    return [...cards].reverse().find((c) => c.result !== onScreen) ?? null
  }
  return null
}
