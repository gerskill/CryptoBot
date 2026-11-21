import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useAlertMonitor } from './useAlertMonitor'
import { useAlertStore } from './alertStore'
import { useStore } from './store'
import type { Arm } from './types'

const requestPermission = vi.fn()
const notifyCtor = vi.fn()

beforeEach(() => {
  document.title = 'CryptoBot'
  vi.stubGlobal('Notification', Object.assign(notifyCtor, { permission: 'granted', requestPermission }))
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  useAlertStore.setState({
    settings: { enabled: true, lossPct: 5, sound: false },
    permission: 'granted',
    alerts: [],
  })
  const arm = { name: 'sniper', enabled: true, stats: { baseline: 1000, total_pnl_usd: -200 } } as unknown as Arm
  useStore.setState({ connected: true, state: { bot_online: true, arms: [arm] } })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  notifyCtor.mockClear()
})

describe('useAlertMonitor', () => {
  it('crée une alerte de bras en perte, notifie une fois et met le compteur dans le titre', () => {
    renderHook(() => useAlertMonitor())
    expect(useAlertStore.getState().alerts.map((a) => a.key)).toEqual(['arm-loss:sniper'])
    expect(notifyCtor).toHaveBeenCalledTimes(1)
    expect(document.title).toBe('(1) CryptoBot')
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it('ne répète pas la même alerte quand l\'état est repoussé', () => {
    renderHook(() => useAlertMonitor())
    act(() => useStore.setState({ state: { ...useStore.getState().state } }))
    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(notifyCtor).toHaveBeenCalledTimes(1)
  })

  it('désactivé : aucune alerte, titre intact', () => {
    useAlertStore.setState({ settings: { enabled: false, lossPct: 5, sound: false } })
    renderHook(() => useAlertMonitor())
    expect(useAlertStore.getState().alerts).toEqual([])
    expect(document.title).toBe('CryptoBot')
  })

  it('bot arrêté : alerte dédiée', () => {
    useStore.setState({ state: { bot_online: false, reason: 'x' } })
    renderHook(() => useAlertMonitor())
    expect(useAlertStore.getState().alerts.map((a) => a.key)).toContain('bot-stopped')
  })
})
