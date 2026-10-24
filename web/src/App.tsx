import { useEffect } from 'react'
import { AppShell } from './components/layout/AppShell'
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

  useEffect(() => {
    void loadBuildings()
    void loadBuilding(initialBuildingId())
  }, [loadBuildings, loadBuilding])

  return <AppShell />
}
