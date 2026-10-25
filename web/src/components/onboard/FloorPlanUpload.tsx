// Facility-manager demo, scene 2.1 (docs/synera/pitch/DEMO_PLAN.md): attach floor-plan pictures, hit Enter.
// View only: nothing is uploaded or processed. The reconstruction video plays after Enter; then `onDone`
// opens the app on the building.
import clsx from 'clsx'
import { useEffect, useRef, useState } from 'react'

export const DEMO_BUILDING = 'munich-urban-colab'
const PLANS = [0, 1, 2, 3, 4].map((i) => ({ src: `/demo/mucl-plans/floor_${i}.svg`, name: `MUCL_Grundriss_${i}.png` }))
const STEPS = ['Reading 5 floor plans', 'Tracing walls and doors', 'Naming 183 spaces', 'Stacking floors in 3D']

export function FloorPlanUpload({ onDone }: { onDone: () => void }) {
  const [files, setFiles] = useState(PLANS)
  const [text, setText] = useState('Here are the floor plans of the Munich Urban Colab. Please set up the building.')
  const [sent, setSent] = useState(false)
  const [step, setStep] = useState(0)
  const pick = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!sent) return
    const t = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length)), 1400)
    return () => clearInterval(t)
  }, [sent])

  const ready = sent && step >= STEPS.length
  useEffect(() => {
    if (!ready) return
    const onKey = (e: KeyboardEvent) => e.key === 'Enter' && onDone()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ready, onDone])

  const add = (list: FileList | null) => {
    const imgs = [...(list ?? [])].filter((f) => f.type.startsWith('image/'))
    if (imgs.length) setFiles((fs) => [...fs, ...imgs.map((f) => ({ src: URL.createObjectURL(f), name: f.name }))])
  }

  return (
    <div
      className="fixed inset-0 flex items-center justify-center overflow-y-auto bg-map-bg px-4 py-10 text-ink"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault()
        add(e.dataTransfer.files)
      }}
    >
      <div className="w-full max-w-3xl">
        <div className="mb-8 flex items-center gap-3">
          <div className="guide-orb anim-breathe h-9 w-9 rounded-full" />
          <div>
            <h1 className="font-display text-2xl font-semibold">Add your building</h1>
            <p className="text-sm text-ink-soft">Drop in photos or scans of your floor plans. We build the rest.</p>
          </div>
        </div>

        {!sent ? (
          <form
            className="anim-rise rounded-3xl bg-surface p-4 shadow-[0_12px_40px_rgb(43_38_64/0.10)]"
            onSubmit={(e) => {
              e.preventDefault()
              if (files.length) setSent(true)
            }}
          >
            <Thumbs files={files} onRemove={(i) => setFiles((fs) => fs.filter((_, j) => j !== i))} />
            <div className="mt-3 flex items-end gap-2">
              <button
                type="button"
                onClick={() => pick.current?.click()}
                className="h-11 w-11 shrink-0 rounded-full bg-accent-soft text-xl text-ink-soft hover:bg-line"
                aria-label="Attach floor plans"
              >
                +
              </button>
              <input ref={pick} type="file" accept="image/*" multiple hidden onChange={(e) => add(e.target.files)} />
              <textarea
                autoFocus
                rows={2}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    e.currentTarget.form?.requestSubmit()
                  }
                }}
                className="min-h-11 flex-1 resize-none rounded-2xl bg-map-bg px-4 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-sun/40"
              />
              <button
                type="submit"
                className="h-11 shrink-0 rounded-full bg-sun px-5 font-semibold text-accent shadow-[0_6px_18px_rgb(255_138_61/0.35)]"
              >
                Build it
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="anim-spring ml-auto max-w-xl rounded-3xl rounded-br-lg bg-accent p-3 text-white">
              <Thumbs files={files} small />
              <p className="px-1 pt-2 text-[15px]">{text}</p>
            </div>
            <div className="anim-rise max-w-md rounded-3xl rounded-bl-lg bg-surface p-5 shadow-[0_12px_40px_rgb(43_38_64/0.10)]">
              <ul className="space-y-2.5">
                {STEPS.map((s, i) => (
                  <li key={s} className={clsx('flex items-center gap-3 text-[15px]', i > step && 'opacity-35')}>
                    <span
                      className={clsx(
                        'flex h-6 w-6 items-center justify-center rounded-full text-xs',
                        i < step ? 'bg-mint-soft text-mint' : i === step ? 'guide-orb anim-breathe' : 'bg-accent-soft',
                      )}
                    >
                      {i < step ? '✓' : ''}
                    </span>
                    {s}
                  </li>
                ))}
              </ul>
              {ready && (
                <button
                  onClick={onDone}
                  className="anim-spring mt-5 w-full rounded-full bg-sun py-3 font-semibold text-accent shadow-[0_6px_18px_rgb(255_138_61/0.35)]"
                >
                  Open Munich Urban Colab
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Thumbs({
  files,
  small,
  onRemove,
}: {
  files: { src: string; name: string }[]
  small?: boolean
  onRemove?: (i: number) => void
}) {
  return (
    <div className={clsx('grid grid-cols-5', small ? 'gap-1.5' : 'gap-2.5')}>
      {files.map((f, i) => (
        <figure
          key={f.src}
          className={clsx(
            'group relative aspect-[1200/520] overflow-hidden bg-white shadow-[0_2px_10px_rgb(43_38_64/0.12)]',
            small ? 'rounded-lg' : 'rounded-xl',
          )}
          style={{ transform: `rotate(${((i % 3) - 1) * 1.2}deg)` }}
          title={f.name}
        >
          <img src={f.src} alt={f.name} className="h-full w-full object-contain" />
          {onRemove && (
            <button
              type="button"
              onClick={() => onRemove(i)}
              className="absolute right-1 top-1 hidden h-6 w-6 rounded-full bg-accent/80 text-xs text-white group-hover:block"
              aria-label={`Remove ${f.name}`}
            >
              ×
            </button>
          )}
        </figure>
      ))}
    </div>
  )
}
