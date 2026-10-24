// One chat message: user bubble right; assistant prose left (no bubble) with "understood" tool chips and the
// benchmark answer as a result chip. Reasoning parts are hidden.
import { getToolName, isToolUIPart, type DynamicToolUIPart, type ToolUIPart } from 'ai'
import { Accessibility, CircleAlert, Check, DoorOpen, LoaderCircle, Lock, Route, Search, Wrench } from 'lucide-react'
import clsx from 'clsx'
import { useMemo } from 'react'
import { useApp } from '../../store'
import type { ChatMessage } from './chats'
import { Markdown } from './Markdown'
import { prettifyIds, splitAnswer, stripPreferences, toolLabel } from './text'
import { toolEffect } from './viewSync'

type ToolPart = ToolUIPart | DynamicToolUIPart

function toolIcon(name: string) {
  if (name === 'find' || name.includes('search')) return Search
  if (name.includes('route') || name === 'walk_directions' || name === 'nearest' || name === 'reachable_rooms') return Route
  if (name.includes('door') || name === 'what_if') return DoorOpen
  if (name.includes('close')) return Lock
  return Wrench
}

export function ToolChip({ part }: { part: ToolPart }) {
  const index = useApp((s) => s.index)
  const name = getToolName(part)
  const done = part.state === 'output-available' || part.state === 'output-error'
  const effect = useMemo(
    () =>
      part.state === 'output-available'
        ? toolEffect(name, part.input, part.output, index)
        : part.state === 'output-error'
          ? toolEffect(name, part.input, undefined, index, part.errorText)
          : null,
    [name, part.state, part.input, part.output, part.errorText, index],
  )
  const failed = !!effect?.error
  const Icon = !done ? LoaderCircle : failed ? CircleAlert : toolIcon(name)
  return (
    <span
      title={effect?.error ?? undefined}
      className={clsx(
        'inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
        failed ? 'border-line bg-surface text-ink-soft' : 'border-accent-soft bg-accent-soft text-accent',
      )}
    >
      <Icon className={clsx('size-3.5 shrink-0', !done && 'animate-spin')} aria-hidden />
      <span className="truncate">{toolLabel(name, part.input, index)}</span>
      {effect?.summary && <span className="shrink-0 opacity-70">· {effect.summary}</span>}
    </span>
  )
}

function UserBubble({ text }: { text: string }) {
  const { text: shown, stepFree } = stripPreferences(text)
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent-soft px-3.5 py-2 text-[15px] whitespace-pre-wrap text-ink">
        {shown}
        {stepFree && (
          <span className="mt-1 flex items-center gap-1 text-xs text-accent">
            <Accessibility className="size-3.5" aria-hidden /> step-free
          </span>
        )}
      </div>
    </div>
  )
}

export function AnswerChip({ value }: { value: string }) {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm shadow-sm">
      <Check className="size-4 text-accent" aria-hidden />
      <span className="text-ink-soft">Answer</span>
      <span className="font-semibold text-ink">{value}</span>
    </div>
  )
}

/** Group consecutive tool parts into one chip row; text parts render as markdown. */
function AssistantMessage({ message }: { message: ChatMessage }) {
  const index = useApp((s) => s.index)
  const groups: ({ kind: 'tools'; parts: ToolPart[] } | { kind: 'text'; text: string })[] = []
  let answer: string | null = null
  for (const part of message.parts) {
    if (isToolUIPart(part)) {
      const last = groups[groups.length - 1]
      if (last?.kind === 'tools') last.parts.push(part)
      else groups.push({ kind: 'tools', parts: [part] })
    } else if (part.type === 'text' && part.text.trim()) {
      const split = splitAnswer(part.text)
      if (split.answer) answer = split.answer
      if (split.body.trim()) groups.push({ kind: 'text', text: prettifyIds(split.body, index) })
    }
  }
  return (
    <div className="space-y-2">
      {groups.map((g, i) =>
        g.kind === 'tools' ? (
          <div key={i} className="flex flex-wrap gap-1.5">
            {g.parts.map((p) => <ToolChip key={p.toolCallId} part={p} />)}
          </div>
        ) : (
          <Markdown key={i} text={g.text} />
        ),
      )}
      {answer && <AnswerChip value={prettifyIds(answer, index)} />}
    </div>
  )
}

export function MessageView({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    const text = message.parts.map((p) => (p.type === 'text' ? p.text : '')).join('')
    return <UserBubble text={text} />
  }
  if (message.role === 'assistant') return <AssistantMessage message={message} />
  return null
}

export function Thinking({ label = 'Thinking' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-soft" role="status">
      <span className="inline-flex gap-1" aria-hidden>
        <span className="size-1.5 animate-bounce rounded-full bg-ink-soft [animation-delay:-0.3s]" />
        <span className="size-1.5 animate-bounce rounded-full bg-ink-soft [animation-delay:-0.15s]" />
        <span className="size-1.5 animate-bounce rounded-full bg-ink-soft" />
      </span>
      <span>{label}…</span>
    </div>
  )
}
