// The stage opener: the welcome page greyed out behind one big pulsing mic. A tap anywhere starts the demo.
import { Mic } from 'lucide-react'
import { useState } from 'react'

export function ShowIntro({ onStart }: { onStart: () => void }) {
  const [leaving, setLeaving] = useState(false)
  const start = () => {
    if (leaving) return
    setLeaving(true)
    window.setTimeout(onStart, 280)
  }
  return (
    <button
      type="button"
      aria-label="Ask the building"
      onClick={start}
      className="show-intro fixed inset-0 z-[200] flex cursor-pointer items-center justify-center"
      data-leaving={leaving || undefined}
    >
      <span className="show-mic relative flex size-40 items-center justify-center rounded-full">
        <span className="show-halo" />
        <span className="show-halo [animation-delay:0.6s]" />
        <span className="show-halo [animation-delay:1.2s]" />
        <span className="show-mic-core relative flex size-40 items-center justify-center rounded-full text-white">
          <Mic className="size-16" strokeWidth={2.2} aria-hidden />
        </span>
      </span>
    </button>
  )
}
