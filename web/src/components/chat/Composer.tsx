// Composer pinned at the bottom of the chat: step-free chip, text input, send / stop.
import { Accessibility, ArrowUp, Square } from 'lucide-react'
import clsx from 'clsx'
import { useState, type FormEvent } from 'react'
import { useApp } from '../../store'

interface ComposerProps {
  busy: boolean
  disabled?: boolean
  onSend: (text: string) => void
  onStop: () => void
}

const isPhone = () => typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches

export function Composer({ busy, disabled, onSend, onStop }: ComposerProps) {
  const [text, setText] = useState('')
  const stepFree = useApp((s) => s.stepFree)
  const setStepFree = useApp((s) => s.setStepFree)
  const setSheet = useApp((s) => s.setSheet)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t || disabled) return
    onSend(t)
    setText('')
  }

  return (
    <form onSubmit={submit} className="border-t border-line bg-surface px-3 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
      <div className="mb-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setStepFree(!stepFree)}
          aria-pressed={stepFree}
          className={clsx(
            'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors',
            stepFree ? 'border-accent bg-accent-soft text-accent' : 'border-line text-ink-soft hover:bg-map-bg',
          )}
        >
          <Accessibility className="size-3.5" aria-hidden />
          Step-free
        </button>
      </div>
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => isPhone() && setSheet('full')}
          placeholder="Ask about this building"
          aria-label="Ask about this building"
          enterKeyHint="send"
          disabled={disabled}
          className="min-w-0 flex-1 rounded-full border border-line bg-map-bg px-4 py-2.5 text-[16px] text-ink outline-none placeholder:text-ink-soft focus:border-accent focus:bg-surface"
        />
        {/* Voice slot: a mic button (speech-to-text into `text`, then onSend) goes here. Not implemented yet. */}
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop"
            className="flex size-10 shrink-0 items-center justify-center rounded-full bg-ink text-white"
          >
            <Square className="size-4 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send"
            disabled={!text.trim() || disabled}
            className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent text-white disabled:bg-line disabled:text-ink-soft"
          >
            <ArrowUp className="size-5" />
          </button>
        )}
      </div>
    </form>
  )
}
