// OWNER: layout agent. Phone: full-screen map behind a draggable bottom sheet. md+: map left, sidebar right.
// One DOM tree for both, so map and panels never remount when the breakpoint flips (rotation, resizing).
import clsx from 'clsx'
import { useCallback, useEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { useApp } from '../../store'
import { BrowsePanel } from '../browse/BrowsePanel'
import { SearchBar } from '../browse/SearchBar'
import { ChatPanel } from '../chat/ChatPanel'
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

  // Switching to Ask while the sheet only peeks: open it so the conversation is visible.
  useEffect(() => {
    if (phone && mode === 'chat' && useApp.getState().sheet === 'peek') setSheet('half')
  }, [phone, mode, setSheet])

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
        {/* Both panels stay mounted: chat history, inputs and scroll positions survive mode switches. */}
        <Panel active={mode === 'browse'}>
          <BrowsePanel />
        </Panel>
        <Panel active={mode === 'chat'}>
          <ChatPanel />
        </Panel>
      </BottomSheet>
    </div>
  )
}

function Panel({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div
      role="tabpanel"
      aria-hidden={!active || undefined}
      inert={!active || undefined}
      className={clsx(
        'absolute inset-0 flex flex-col overflow-y-auto overscroll-contain',
        !active && 'invisible pointer-events-none',
      )}
    >
      {children}
    </div>
  )
}
