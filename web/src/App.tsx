import { useCallback, useEffect, useState } from 'react'
import { AppShell } from './components/layout/AppShell'
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
  // ?demo=fm starts the facility-manager demo on the floor-plan upload view (docs/synera/pitch/DEMO_PLAN.md).
  const [onboarding, setOnboarding] = useState(() => new URL(window.location.href).searchParams.get('demo') === 'fm')

  useEffect(() => {
    void loadBuildings()
    void loadBuilding(onboarding ? DEMO_BUILDING : initialBuildingId())
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the first building only
  }, [loadBuildings, loadBuilding])

  const finishOnboarding = useCallback(() => {
    const url = new URL(window.location.href)
    url.searchParams.delete('demo')
    url.searchParams.set('b', DEMO_BUILDING)
    window.history.replaceState(null, '', url)
    setOnboarding(false)
  }, [])

  return onboarding ? <FloorPlanUpload onDone={finishOnboarding} /> : <AppShell />
}
