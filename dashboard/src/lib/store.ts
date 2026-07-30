import { create } from 'zustand'
import type { BotState, Trade } from './types'

/**
 * État du dashboard. Le WebSocket pousse l'état du bot ; les trades et les
 * shadow trades sont récupérés en REST à l'ouverture puis après chaque
 * changement de nombre de trades.
 */
type Store = {
  state: BotState
  trades: Trade[]
  shadow: { total: number; missed: number; missed_rate: number }
  connected: boolean
  setState: (state: BotState) => void
  setTrades: (trades: Trade[]) => void
  setShadow: (shadow: Store['shadow']) => void
  setConnected: (connected: boolean) => void
}

export const useStore = create<Store>((set) => ({
  state: { bot_online: false },
  trades: [],
  shadow: { total: 0, missed: 0, missed_rate: 0 },
  connected: false,
  setState: (state) => set({ state }),
  setTrades: (trades) => set({ trades }),
  setShadow: (shadow) => set({ shadow }),
  setConnected: (connected) => set({ connected }),
}))
