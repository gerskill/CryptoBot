import { create } from 'zustand'
import type { BotState, PerformanceWindow, Trade } from './types'
import { DEFAULT_URL_STATE } from './urlState'
import type { FilterKey, Filters, UrlState } from './urlState'

export type View = 'dashboard' | 'live' | 'weekly' | 'config' | 'backtester' | 'pilotage'
/** Les deux espaces du site : le bot memecoins et Liquid. Chacun a ses onglets. */
export type Space = 'memecoins' | 'liquid'
export type LiquidView = 'apercu' | 'predictions' | 'marches' | 'paris' | 'incoherences'

/**
 * État du dashboard. Le WebSocket pousse l'état du bot ; les trades et les
 * shadow trades sont récupérés en REST à l'ouverture puis après chaque
 * changement de nombre de trades.
 */
type Store = {
  state: BotState
  trades: Trade[]
  // P&L de CHAQUE trade clôturé, ordre chronologique, JAMAIS tronqué par la
  // pagination de `trades`. `EquityCurve` en a besoin pour recoller avec le
  // capital affiché dans l'en-tête : sommer une fenêtre tronquée produisait
  // un point final décalé de ~47 $ par rapport à l'équité réelle.
  equitySeries: number[]
  shadow: { total: number; missed: number; missed_rate: number }
  connected: boolean
  view: View
  space: Space
  liquidView: LiquidView
  /** Exemple fictif affiché à la place des données réelles (espace Liquid). */
  liquidDemo: boolean
  /** Navigation : portée par l'URL (voir urlState.ts et useUrlSync.ts). */
  window: PerformanceWindow
  /** Panneau déplié (nom du bras). */
  panel: string | null
  filters: Filters
  paletteOpen: boolean
  helpOpen: boolean
  shortcutsEnabled: boolean
  /** Message lu par les lecteurs d'écran (role="status"). */
  announcement: string
  applyUrlState: (next: Partial<UrlState>) => void
  setWindow: (window: PerformanceWindow) => void
  setPanel: (panel: string | null) => void
  setFilter: (key: FilterKey, value: string | null) => void
  setPaletteOpen: (open: boolean) => void
  setHelpOpen: (open: boolean) => void
  setShortcutsEnabled: (enabled: boolean) => void
  announce: (message: string) => void
  setState: (state: BotState) => void
  setTrades: (trades: Trade[]) => void
  setEquitySeries: (series: number[]) => void
  setShadow: (shadow: Store['shadow']) => void
  setConnected: (connected: boolean) => void
  setView: (view: View) => void
  setSpace: (space: Space) => void
  setLiquidView: (liquidView: LiquidView) => void
  setLiquidDemo: (liquidDemo: boolean) => void
}

export const useStore = create<Store>((set) => ({
  state: { bot_online: false },
  trades: [],
  equitySeries: [],
  shadow: { total: 0, missed: 0, missed_rate: 0 },
  connected: false,
  view: 'dashboard',
  space: 'memecoins',
  liquidView: 'apercu',
  liquidDemo: false,
  window: DEFAULT_URL_STATE.window,
  panel: null,
  filters: {},
  paletteOpen: false,
  helpOpen: false,
  shortcutsEnabled: true,
  announcement: '',
  applyUrlState: (next) => set((s) => ({
    ...next,
    // Une cible de navigation peut passer par le même chemin que l'URL.
    filters: next.filters ?? s.filters,
  })),
  setWindow: (window) => set({ window }),
  setPanel: (panel) => set({ panel }),
  setFilter: (key, value) => set((s) => {
    const rest = Object.fromEntries(Object.entries(s.filters).filter(([k]) => k !== key))
    return { filters: value ? { ...rest, [key]: value } : rest }
  }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  setHelpOpen: (helpOpen) => set({ helpOpen }),
  setShortcutsEnabled: (shortcutsEnabled) => set({ shortcutsEnabled }),
  announce: (announcement) => set({ announcement }),
  setState: (state) => set({ state }),
  setTrades: (trades) => set({ trades }),
  setEquitySeries: (equitySeries) => set({ equitySeries }),
  setShadow: (shadow) => set({ shadow }),
  setConnected: (connected) => set({ connected }),
  // Changer d'onglet ou d'espace efface le contexte local (panneau, filtres) :
  // il n'a de sens que dans la vue où il a été posé.
  setView: (view) => set((s) => (s.view === view ? {} : { view, panel: null, filters: {} })),
  setSpace: (space) => set((s) => (s.space === space ? {} : { space, panel: null, filters: {} })),
  setLiquidView: (liquidView) => set((s) => (s.liquidView === liquidView ? {} : { liquidView, panel: null, filters: {} })),
  setLiquidDemo: (liquidDemo) => set({ liquidDemo }),
}))
