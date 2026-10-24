// Viewport facts the layout needs in JS: phone vs split breakpoint, inner height and safe-area insets.
import { useEffect, useState, useSyncExternalStore } from 'react'

const SPLIT_QUERY = '(min-width: 768px)'

/** True below the md breakpoint (768px): map full-screen with a bottom sheet. iPad portrait and up use split view. */
export function useIsPhone(): boolean {
  const wide = useSyncExternalStore(
    (cb) => {
      const mq = window.matchMedia(SPLIT_QUERY)
      mq.addEventListener('change', cb)
      return () => mq.removeEventListener('change', cb)
    },
    () => window.matchMedia(SPLIT_QUERY).matches,
    () => true,
  )
  return !wide
}

export interface Viewport {
  vh: number
  safeTop: number
  safeBottom: number
}

function readInsets(): { safeTop: number; safeBottom: number } {
  const probe = document.createElement('div')
  probe.style.cssText =
    'position:fixed;top:0;left:0;visibility:hidden;pointer-events:none;' +
    'padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)'
  document.body.appendChild(probe)
  const cs = getComputedStyle(probe)
  const out = { safeTop: parseFloat(cs.paddingTop) || 0, safeBottom: parseFloat(cs.paddingBottom) || 0 }
  probe.remove()
  return out
}

function read(): Viewport {
  return { vh: window.innerHeight, ...readInsets() }
}

/** Layout viewport height (= 100dvh on a fixed full-screen root) and safe-area insets, live on resize/rotation. */
export function useViewport(): Viewport {
  const [vp, setVp] = useState<Viewport>(read)
  useEffect(() => {
    let raf = 0
    const update = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const next = read()
        setVp((prev) =>
          prev.vh === next.vh && prev.safeTop === next.safeTop && prev.safeBottom === next.safeBottom ? prev : next,
        )
      })
    }
    window.addEventListener('resize', update)
    window.addEventListener('orientationchange', update)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', update)
      window.removeEventListener('orientationchange', update)
    }
  }, [])
  return vp
}
