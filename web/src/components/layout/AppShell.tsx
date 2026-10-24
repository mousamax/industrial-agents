// OWNER: layout agent.
// Default mode ('chat') renders the Agent-First workspace (AgentPhone on <768px, AgentDesktop on md+).
// Floor-plan mode ('browse') renders the full-screen map explorer with SearchBar, FloorPicker & BrowsePanel.
import clsx from 'clsx'
import { useCallback, useRef, type CSSProperties } from 'react'
import { useApp } from '../../store'
import { AgentWorkspace } from '../agent/AgentWorkspace'
import { BrowsePanel } from '../browse/BrowsePanel'
import { SearchBar } from '../browse/SearchBar'
import { FloorPicker } from '../map/FloorPicker'
import { MapView } from '../map/MapView'
import { BottomSheet } from './BottomSheet'
import { ModeTabs } from './ModeTabs'
import { SheetSummary } from './SheetSummary'
import { StatusOverlay } from './StatusOverlay'
import { useIsPhone } from './useViewport'

export function AppShell() {
  const phone = useIsPhone()
  const mode = useApp((s) => s.mode)
  const setSheet = useApp((s) => s.setSheet)
  const rootRef = useRef<HTMLDivElement>(null)

  // --sheet-offset: px of the viewport the sheet covers (0 in split view). Written directly, every drag frame.
  const onOffset = useCallback((px: number) => {
    rootRef.current?.style.setProperty('--sheet-offset', `${px}px`)
  }, [])

  if (mode === 'chat') {
    return <AgentWorkspace phone={phone} />
  }

  return (
    <div
      ref={rootRef}
      className="fixed inset-0 flex h-dvh w-full overflow-clip overscroll-none bg-map-bg"
      style={{ '--sheet-offset': '0px' } as CSSProperties}
    >
      {/* isolate: map-internal z-indexes can never climb above the sheet. */}
      <main className="relative isolate h-full min-w-0 flex-1" aria-label="Building map">
        <MapView />
        <FloorPicker />
        <StatusOverlay />
      </main>

      {/* Outside the map so it floats above a peek/half sheet (z-30 vs 20); a nearly-full sheet goes to 40. */}
      <div
        className={clsx(
          'pointer-events-none absolute top-[calc(12px+env(safe-area-inset-top))] z-30 [&>*]:pointer-events-auto',
          phone ? 'inset-x-3' : 'left-3 w-[420px] max-w-[calc(100%-404px)] xl:max-w-[calc(100%-444px)]',
        )}
      >
        <SearchBar />
      </div>

      <BottomSheet
        phone={phone}
        onOffset={onOffset}
        header={<ModeTabs />}
        peek={<SheetSummary onOpen={() => setSheet('half')} />}
      >
        <div
          role="tabpanel"
          className="absolute inset-0 flex flex-col overflow-y-auto overscroll-contain"
        >
          <BrowsePanel />
        </div>
      </BottomSheet>
    </div>
  )
}
