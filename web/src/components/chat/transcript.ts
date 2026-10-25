// A plain-text transcript of the conversation for debugging: every message to and from the building guide with
// its tool calls (arguments and results), and the live voice call's own side (what was heard, what the voice
// agent said, its client tools). The "Copy transcript" button puts it on the clipboard.
import { getToolName, isToolUIPart } from 'ai'
import type { ChatMessage } from './chats'

/** Tool results can carry long polylines; each is cut to this many characters. */
const MAX_OUTPUT = 3000

interface VoiceEntry {
  at: Date
  text: string
}

const voiceLog: VoiceEntry[] = []

/** One line of the voice call: "heard: ...", "voice agent: ...", "tool show_on_screen(...) -> ...". */
export function logVoice(text: string) {
  voiceLog.push({ at: new Date(), text })
}

export function clearVoiceLog() {
  voiceLog.length = 0
}

const clip = (s: string) => (s.length > MAX_OUTPUT ? `${s.slice(0, MAX_OUTPUT)} … [${s.length - MAX_OUTPUT} more chars]` : s)
const json = (v: unknown) => {
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}
const time = (d: Date) => d.toLocaleTimeString([], { hour12: false })

export function transcriptText(buildingId: string | null, messages: ChatMessage[]): string {
  const lines = [
    `# Transcript · building ${buildingId ?? 'none'} · ${new Date().toISOString()}`,
    `${window.location.href}`,
    '',
    '## Building guide',
  ]
  if (!messages.length) lines.push('(no messages)')
  for (const m of messages) {
    lines.push('', `### ${m.role}`)
    for (const p of m.parts) {
      if (p.type === 'text' && p.text.trim()) lines.push(p.text.trim())
      else if (p.type === 'reasoning' && p.text.trim()) lines.push(`[thinking] ${p.text.trim()}`)
      else if (isToolUIPart(p)) {
        lines.push(`→ ${getToolName(p)}(${json(p.input)})`)
        if (p.state === 'output-available') lines.push(`  ← ${clip(json(p.output))}`)
        else if (p.state === 'output-error') lines.push(`  ← ERROR ${p.errorText}`)
        else lines.push(`  ← (${p.state})`)
      }
    }
  }
  if (voiceLog.length) {
    lines.push('', '## Voice call')
    for (const e of voiceLog) lines.push(`${time(e.at)} ${e.text}`)
  }
  return lines.join('\n')
}

/** Copies the transcript; false when the browser refused the clipboard. */
export async function copyTranscript(buildingId: string | null, messages: ChatMessage[]): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(transcriptText(buildingId, messages))
    return true
  } catch {
    return false
  }
}
