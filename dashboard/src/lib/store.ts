import { create } from 'zustand'
import type { BotState, Trade } from './types'

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
  setState: (state) => set({ state }),
  setTrades: (trades) => set({ trades }),
  setEquitySeries: (equitySeries) => set({ equitySeries }),
  setShadow: (shadow) => set({ shadow }),
  setConnected: (connected) => set({ connected }),
  setView: (view) => set({ view }),
  setSpace: (space) => set({ space }),
  setLiquidView: (liquidView) => set({ liquidView }),
  setLiquidDemo: (liquidDemo) => set({ liquidDemo }),
}))
