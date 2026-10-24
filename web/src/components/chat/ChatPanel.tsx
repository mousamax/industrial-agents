// OWNER: chat agent. The "talk to your building" panel: fills its parent (phone bottom sheet or md+ sidebar).
// The Chat lives in chats.ts (outside React), so messages and map sync survive this panel being unmounted.
import { useChat } from '@ai-sdk/react'
import { CircleAlert, MessageSquare, RefreshCw, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useRef } from 'react'
import { useApp } from '../../store'
import { resetChat, useBuildingChat, type BuildingChat } from './chats'
import { Composer } from './Composer'
import { MessageView, Thinking } from './Message'
import { RouteCard } from './RouteCard'
import { withPreferences } from './text'

const STARTERS = [
  'Where can I get a coffee?',
  'Take me to the nearest toilet',
  'Which rooms can I reach without stairs?',
  'The lift is closed — what changes?',
]

export function ChatPanel() {
  const buildingId = useApp((s) => s.buildingId)
  const chat = useBuildingChat(buildingId)
  if (!buildingId || !chat) {
    return <div className="flex h-full items-center justify-center p-4 text-sm text-ink-soft">Loading building…</div>
  }
  return <ChatView key={chat.id} buildingId={buildingId} chat={chat} />
}

function errorText(error: Error): string {
  const msg = error.message || String(error)
  if (/failed to fetch|networkerror|load failed/i.test(msg)) return 'The building guide is not reachable. Is the backend running?'
  if (/api[_ ]?key|authentication|401/i.test(msg)) return 'The guide has no model API key configured.'
  return msg.length > 240 ? msg.slice(0, 240) + '…' : msg
}

function ChatView({ buildingId, chat }: { buildingId: string; chat: BuildingChat }) {
  const { messages, status, error, sendMessage, regenerate, stop, clearError } = useChat({ chat })
  const pendingPrompt = useApp((s) => s.pendingPrompt)
  const consumePrompt = useApp((s) => s.consumePrompt)
  const busy = status === 'submitted' || status === 'streaming'

  const send = useCallback(
    async (text: string) => {
      if (chat.status === 'submitted' || chat.status === 'streaming') await chat.stop()
      clearError()
      void sendMessage({ text: withPreferences(text, useApp.getState().stepFree) })
    },
    [chat, sendMessage, clearError],
  )

  // Prompts from other panels ("Directions" on a place card): send on mount and whenever one arrives.
  useEffect(() => {
    const text = consumePrompt()
    if (text) void send(text)
  }, [pendingPrompt, consumePrompt, send])

  // Keep the newest message in view while the user is near the bottom.
  const scroller = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  useEffect(() => {
    const el = scroller.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }, [messages, status])

  const last = messages[messages.length - 1]
  const lastPart = last?.parts[last.parts.length - 1]
  const waiting =
    status === 'submitted' ||
    (status === 'streaming' && (last?.role !== 'assistant' || !lastPart || lastPart.type === 'reasoning' || lastPart.type === 'step-start'))

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      {messages.length > 0 && (
        <div className="flex items-center justify-end px-3 pt-2">
          <button
            type="button"
            onClick={() => resetChat(buildingId)}
            className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs text-ink-soft hover:bg-map-bg"
          >
            <RotateCcw className="size-3.5" aria-hidden /> New chat
          </button>
        </div>
      )}

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
        }}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-3"
      >
        {messages.length === 0 ? (
          <EmptyState onPick={(t) => void send(t)} />
        ) : (
          messages.map((m) => <MessageView key={m.id} message={m} />)
        )}
        {waiting && <Thinking />}
        {error && (
          <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm" role="alert">
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-red-600" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-red-700">Something went wrong</p>
              <p className="mt-0.5 break-words text-ink-soft">{errorText(error)}</p>
            </div>
            <button
              type="button"
              onClick={() => {
                clearError()
                void regenerate()
              }}
              className="inline-flex shrink-0 items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-medium text-ink"
            >
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </button>
          </div>
        )}
      </div>

      <RouteCard />
      <Composer busy={busy} onSend={(t) => void send(t)} onStop={() => void stop()} />
    </div>
  )
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="flex flex-col items-start gap-3 pt-2">
      <div className="flex items-center gap-2 text-ink">
        <MessageSquare className="size-5 text-accent" aria-hidden />
        <p className="text-base font-medium">Ask your building</p>
      </div>
      <p className="text-sm text-ink-soft">Find a room or thing, get directions, or tell it what changed.</p>
      <div className="flex flex-wrap gap-2">
        {STARTERS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-full border border-line px-3 py-1.5 text-left text-sm text-ink hover:bg-accent-soft hover:text-accent"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
