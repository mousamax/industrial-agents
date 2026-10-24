// Browse-local UI state that several browse components share (the building picker).
import { create } from 'zustand'

export interface BrowseUi {
  pickerOpen: boolean
  setPickerOpen: (open: boolean) => void
  /** Mounted BuildingPicker instances; only the first renders, so several components may mount one safely. */
  pickerHosts: string[]
  claimPickerHost: (id: string) => void
  releasePickerHost: (id: string) => void
}

export const useBrowseUi = create<BrowseUi>((set) => ({
  pickerOpen: false,
  setPickerOpen: (pickerOpen) => set({ pickerOpen }),
  pickerHosts: [],
  claimPickerHost: (id) => set((s) => ({ pickerHosts: s.pickerHosts.includes(id) ? s.pickerHosts : [...s.pickerHosts, id] })),
  releasePickerHost: (id) => set((s) => ({ pickerHosts: s.pickerHosts.filter((h) => h !== id) })),
}))
