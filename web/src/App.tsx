import { useEffect } from 'react'
import { AppShell } from './components/layout/AppShell'
import { initialBuildingId, useApp } from './store'

export default function App() {
  const loadBuildings = useApp((s) => s.loadBuildings)
  const loadBuilding = useApp((s) => s.loadBuilding)

  useEffect(() => {
    void loadBuildings()
    void loadBuilding(initialBuildingId())
  }, [loadBuildings, loadBuilding])

  return <AppShell />
}
