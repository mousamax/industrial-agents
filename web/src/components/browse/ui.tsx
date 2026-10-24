// Small shared browse bits: category icons, buttons, chips.
import clsx from 'clsx'
import {
  ArrowUpDown, Bath, BedDouble, BookOpen, Coffee, DoorOpen, Factory, FireExtinguisher, FlaskConical,
  Footprints, GraduationCap, MapPin, Package, Presentation, Printer, Server, Sofa, Trees, Briefcase,
  Utensils, Cog, type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { CategoryIcon } from './browseLib'

const ICONS: Record<CategoryIcon, LucideIcon> = {
  coffee: Coffee,
  toilet: Bath,
  kitchen: Utensils,
  meeting: Presentation,
  entrance: DoorOpen,
  lift: ArrowUpDown,
  stairs: Footprints,
  machine: Cog,
  printer: Printer,
  lab: FlaskConical,
  class: GraduationCap,
  office: Briefcase,
  production: Factory,
  storage: Package,
  safety: FireExtinguisher,
  server: Server,
  bed: BedDouble,
  living: Sofa,
  outdoor: Trees,
  library: BookOpen,
  place: MapPin,
}

export function CategoryGlyph({ icon, className }: { icon: CategoryIcon; className?: string }) {
  const Icon = ICONS[icon] ?? MapPin
  return <Icon className={className ?? 'size-4'} aria-hidden />
}

/** Google-Maps-style action button: filled primary or outlined secondary pill, 44px tall for touch. */
export function ActionButton({
  primary, icon, children, onClick, className,
}: { primary?: boolean; icon?: ReactNode; children: ReactNode; onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-full px-4 text-sm font-medium transition-colors active:scale-[0.98]',
        primary ? 'bg-accent text-white shadow-sm hover:brightness-110' : 'border border-line bg-surface text-accent hover:bg-accent-soft',
        className,
      )}
    >
      {icon}
      {children}
    </button>
  )
}

/** Small round icon button (close, back). */
export function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid size-11 shrink-0 place-items-center rounded-full text-ink-soft hover:bg-black/5 active:bg-black/10"
    >
      {children}
    </button>
  )
}
