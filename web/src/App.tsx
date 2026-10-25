import { useCallback, useEffect, useState } from 'react'
import { AppShell } from './components/layout/AppShell'
import { CalendarConnect } from './components/onboard/CalendarConnect'
import { DEMO_BUILDING, FloorPlanUpload } from './components/onboard/FloorPlanUpload'
import { initialBuildingId, useApp } from './store'

/**
 * Only the map zooms. A pinch or ctrl+wheel anywhere else would make the browser zoom the whole page, which it
 * does by scaling a bitmap: everything goes blurry and the layout no longer fits the screen.
 */
function useBlockPageZoom() {
  useEffect(() => {
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) e.preventDefault() // trackpad pinch arrives as ctrl+wheel; the map handles its own
    }
    const onGesture = (e: Event) => e.preventDefault() // Safari pinch
    window.addEventListener('wheel', onWheel, { passive: false })
    for (const t of ['gesturestart', 'gesturechange']) document.addEventListener(t, onGesture, { passive: false })
    return () => {
      window.removeEventListener('wheel', onWheel)
      for (const t of ['gesturestart', 'gesturechange']) document.removeEventListener(t, onGesture)
    }
  }, [])
}

export default function App() {
  const loadBuildings = useApp((s) => s.loadBuildings)
  const loadBuilding = useApp((s) => s.loadBuilding)
  useBlockPageZoom()
  // The facility-manager demo (docs/synera/pitch/DEMO_PLAN.md): ?demo=fm starts on the floor-plan upload, then
  // connects the building calendar; ?demo=calendar starts at the calendar (rehearsing that part alone).
  const [phase, setPhase] = useState<'upload' | 'calendar' | 'app'>(() => {
    const demo = new URL(window.location.href).searchParams.get('demo')
    return demo === 'fm' ? 'upload' : demo === 'calendar' ? 'calendar' : 'app'
  })

  useEffect(() => {
    void loadBuildings()
    void loadBuilding(phase !== 'app' ? DEMO_BUILDING : initialBuildingId())
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the first building only
  }, [loadBuildings, loadBuilding])

  // `ask`: a question the guide answers as soon as it opens ("Try it as a guest").
  const finishDemo = useCallback((ask?: string) => {
    const url = new URL(window.location.href)
    url.searchParams.delete('demo')
    url.searchParams.set('b', DEMO_BUILDING)
    window.history.replaceState(null, '', url)
    setPhase('app')
    if (ask) useApp.getState().ask(ask)
  }, [])

  if (phase === 'upload') return <FloorPlanUpload onDone={() => setPhase('calendar')} />
  if (phase === 'calendar') return <CalendarConnect buildingId={DEMO_BUILDING} onDone={finishDemo} />
  return <AppShell />
}
