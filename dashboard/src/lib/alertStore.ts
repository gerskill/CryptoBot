import { create } from 'zustand'
import { loadSettings, saveSettings } from './alertSettings'
import { notificationState, type NotificationState } from './notify'
import type { AlertCandidate, AlertEntry, AlertSettings } from './alerts'

const MAX_ALERTS = 50

type AlertStore = {
  settings: AlertSettings
  permission: NotificationState
  alerts: AlertEntry[]
  setSettings: (patch: Partial<AlertSettings>) => void
  setPermission: (permission: NotificationState) => void
  addAlerts: (fresh: readonly AlertCandidate[], at: number) => void
  markRead: (id: string) => void
  markAllRead: () => void
}

let sequence = 0

export const useAlertStore = create<AlertStore>((set) => ({
  settings: loadSettings(),
  permission: notificationState(),
  alerts: [],
  setSettings: (patch) =>
    set((state) => {
      const settings = { ...state.settings, ...patch }
      saveSettings(settings)
      return { settings }
    }),
  setPermission: (permission) => set({ permission }),
  addAlerts: (fresh, at) =>
    set((state) => ({
      alerts: [
        ...fresh.map((alert): AlertEntry => ({ ...alert, id: `${at}-${sequence++}`, at, read: false })),
        ...state.alerts,
      ].slice(0, MAX_ALERTS),
    })),
  markRead: (id) =>
    set((state) => ({ alerts: state.alerts.map((a) => (a.id === id ? { ...a, read: true } : a)) })),
  markAllRead: () => set((state) => ({ alerts: state.alerts.map((a) => ({ ...a, read: true })) })),
}))

export const selectUnread = (state: AlertStore): number => state.alerts.filter((a) => !a.read).length
