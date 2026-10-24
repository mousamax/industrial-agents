// Dev-only tuning panel for the 3D view (shown with `?dev`). Rendered by MapView inside the map container:
// top-left under the search bar on desktop, bottom-left above the sheet on phones. It only covers its own box;
// map gestures elsewhere are untouched. Live readouts poll MapView's camera a few times per second.
import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { DEFAULT_3D, useMap3DSettings } from './devSettings'
import type { Map3DSettings } from './types'

export interface DevStats {
  k: number
  pitchDeg: number
  mode: '2D' | '3D'
  floor: string
  engine: string
  /** Map container width in CSS px. */
  width: number
}

const fmt = (n: number, d = 1) => (Math.round(n * 10 ** d) / 10 ** d).toString()

function Slider({ label, value, min, max, step, onChange, hint }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  hint?: string
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between gap-2">
        <span>{label}</span>
        <span className="font-mono tabular-nums text-ink">
          {fmt(value, step < 1 ? 1 : 0)}
          {hint && <span className="ml-1 text-ink-soft">{hint}</span>}
        </span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[var(--color-accent)]" />
    </label>
  )
}

export function DevPanel({ stats, phone }: { stats: () => DevStats; phone: boolean }) {
  const settings = useMap3DSettings((s) => s.settings)
  const setS = useMap3DSettings((s) => s.set)
  const reset = useMap3DSettings((s) => s.reset)
  const [open, setOpen] = useState(true)
  const [live, setLive] = useState<DevStats>(stats)

  useEffect(() => {
    const id = window.setInterval(() => setLive(stats()), 150)
    return () => window.clearInterval(id)
  }, [stats])

  const set = <K extends keyof Map3DSettings>(key: K) => (v: Map3DSettings[K]) => setS({ [key]: v } as Partial<Map3DSettings>)
  const changed = (Object.keys(DEFAULT_3D) as (keyof Map3DSettings)[]).some((key) => settings[key] !== DEFAULT_3D[key])

  return (
    <div
      className={clsx(
        'absolute left-3 z-20 w-60 overflow-y-auto rounded-lg bg-surface/95 text-[11px] text-ink-soft shadow-md',
        'touch-auto select-none',
      )}
      style={
        phone
          ? { bottom: 'calc(var(--sheet-offset, 0px) + 12px)', maxHeight: 'calc(100% - var(--sheet-offset, 0px) - 136px)' }
          : { top: 120, maxHeight: 'calc(100% - 136px)' }
      }
    >
      <button type="button" onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 font-semibold text-ink">
        <span>3D dev</span>
        <span className="font-mono font-normal tabular-nums text-ink-soft">
          {live.mode} · k {fmt(live.k)} · {fmt(live.pitchDeg, 0)}°
        </span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-2.5 pb-2.5 pt-2">
          <div className="grid grid-cols-2 gap-x-2 font-mono tabular-nums">
            <span>k {fmt(live.k, 2)} px/m</span>
            <span>view ≈ {fmt(live.width / Math.max(live.k, 1e-6))} m</span>
            <span>pitch {fmt(live.pitchDeg)}°</span>
            <span>mode {live.mode}</span>
            <span className="truncate" title={live.floor}>floor {live.floor}</span>
            <span className="truncate">engine {live.engine}</span>
          </div>

          <Slider label="objectsZoomK (px/m)" value={settings.objectsZoomK} min={5} max={200} step={1}
            onChange={set('objectsZoomK')} hint={`≈ ${fmt(live.width / settings.objectsZoomK)} m wide`} />
          <button type="button" onClick={() => setS({ objectsZoomK: Math.round(Math.min(200, Math.max(5, live.k))) })}
            className="w-full rounded bg-accent-soft px-2 py-1 font-semibold text-accent">
            Use current zoom as threshold ({fmt(live.k, 0)})
          </button>
          <Slider label="autoTiltDeg" value={settings.autoTiltDeg} min={0} max={70} step={1}
            onChange={set('autoTiltDeg')} />
          <Slider label="maxPitchDeg" value={settings.maxPitchDeg} min={30} max={80} step={1}
            onChange={set('maxPitchDeg')} />
          <Slider label="fovDeg" value={settings.fovDeg} min={20} max={70} step={1} onChange={set('fovDeg')} />
          <Slider label="wallHeightM" value={settings.wallHeightM} min={0.5} max={4} step={0.1}
            onChange={set('wallHeightM')} />
          <Slider label="floorGapScale" value={settings.floorGapScale} min={0.5} max={3} step={0.1}
            onChange={set('floorGapScale')} />

          <label className="flex items-center justify-between">
            <span>showLowerFloors</span>
            <input type="checkbox" checked={settings.showLowerFloors}
              onChange={(e) => setS({ showLowerFloors: e.target.checked })} />
          </label>
          <div className="flex items-center justify-between">
            <span>objectDetail</span>
            <div className="flex overflow-hidden rounded border border-line">
              {([0, 1, 2] as const).map((d) => (
                <button key={d} type="button" onClick={() => setS({ objectDetail: d })}
                  className={clsx('px-2 py-0.5', settings.objectDetail === d ? 'bg-accent text-white' : 'text-ink')}>
                  {d}
                </button>
              ))}
            </div>
          </div>
          <button type="button" onClick={reset} disabled={!changed}
            className="w-full rounded border border-line px-2 py-1 font-semibold text-ink disabled:opacity-40">
            Reset to defaults
          </button>
        </div>
      )}
    </div>
  )
}
