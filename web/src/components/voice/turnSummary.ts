// What the voice agent hears back from a client tool: the guide's turn (as the screen shows it) boiled down to
// the facts worth saying, without ids or markdown. The voice agent turns this into a spoken sentence or two.
import { floorNameOf, fmtM, fmtWalk, type WalkStep } from '../../lib/buildingGraph'
import type { SceneIndex } from '../../lib/sceneIndex'
import type { LiveExtraction } from '../agent/liveBridge'

const clean = (text: string) =>
  text
    .replace(/\*\*|__|`/g, '')
    .replace(/^\s*[-*]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()

export function summarizeTurn(turn: LiveExtraction, index: SceneIndex | null): string {
  if (turn.ask) {
    const options = turn.ask.options.filter((o) => !o.disabled).map((o) => `${o.label} (${o.sub})`)
    return [
      `The guide asks: ${clean(turn.ask.q)}`,
      turn.ask.why && `Why: ${clean(turn.ask.why)}`,
      options.length > 0 && `Options on screen: ${options.join('; ')}.`,
      'Ask the person, then pass their answer back with ask_building_guide.',
    ]
      .filter(Boolean)
      .join('\n')
  }
  const r = turn.result
  if (!r) return turn.prose ? `The guide says: ${clean(turn.prose)}` : 'The guide had nothing to add.'

  const lines = [`The guide says: ${clean(r.say ?? r.understood)}`, `On screen: ${r.title}${r.sub ? ` · ${r.sub}` : ''}.`]
  if (r.route) {
    const floors = index ? r.route.floors.map((f) => floorNameOf(index, f)).filter(Boolean) : []
    lines.push(
      `Route: ${fmtM(r.route.length)}, ${fmtWalk(r.route.length)} on foot${floors.length > 1 ? `, via ${floors.join(' → ')}` : ''}.`,
    )
    if (r.steps?.length) {
      lines.push(`Steps (on screen, for follow-ups): ${r.steps.map((s, i) => `${i + 1}. ${s.text}`).join(' ')}`)
      lines.push(
        'Say only how far it is and where it ends, with at most one landmark, not the steps; then offer to guide them step by step (start_walk).',
      )
    }
  }
  if (r.stats?.length) lines.push(`Numbers: ${r.stats.map((s) => `${s.n} ${s.label}`).join('; ')}.`)
  if (r.note) lines.push(clean(r.note))
  if (r.confirm && !turn.alreadyApplied) {
    lines.push(`This is a preview. If they want it, confirm with apply_change (button: "${r.confirm}").`)
  } else if (turn.alreadyApplied && r.applied) {
    lines.push(`Recorded: ${r.applied}`)
  }
  return lines.join('\n')
}

export function describeStep(steps: WalkStep[], i: number): string {
  const step = steps[i]
  if (!step) return 'There is no step there.'
  const leg = step.d > 0 ? ` (${fmtM(step.d)})` : ''
  if (i === steps.length - 1) return `Last step, ${i + 1} of ${steps.length}: ${step.text}${leg}. Then they have arrived.`
  const after = steps.slice(i + 1).reduce((a, s) => a + (s.d || 0), 0)
  return `Step ${i + 1} of ${steps.length}: ${step.text}${leg}. After this step, ${fmtM(after)} left.`
}
