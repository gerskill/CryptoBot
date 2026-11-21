import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useAlertMonitor } from './useAlertMonitor'
import { useAlertStore } from './alertStore'
import { useStore } from './store'
import type { Arm } from './types'

const requestPermission = vi.fn()
const notifyCtor = vi.fn()

const armLoss = { name: 'sniper', enabled: true, stats: { baseline: 1000, total_pnl_usd: -200 } } as unknown as Arm
const losingArm = (pnl: number) => ({ ...armLoss, stats: { baseline: 1000, total_pnl_usd: pnl } }) as unknown as Arm

beforeEach(() => {
  sessionStorage.clear()
  document.title = 'CryptoBot'
  vi.stubGlobal('Notification', Object.assign(notifyCtor, { permission: 'granted', requestPermission }))
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  useAlertStore.setState({
    settings: { enabled: true, lossPct: 5, sound: false },
    permission: 'granted',
    alerts: [],
  })
  useStore.setState({
    connected: true,
    lastFrameAt: Date.now(),
    connectedAt: Date.now(),
    state: { bot_online: true, arms: [armLoss] },
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  notifyCtor.mockClear()
})

describe('useAlertMonitor', () => {
  it('amorçage : un bras DÉJÀ en perte à l\'ouverture est listé lu, sans notification ni compteur', () => {
    renderHook(() => useAlertMonitor())
    const alerts = useAlertStore.getState().alerts
    expect(alerts.map((a) => [a.key, a.read])).toEqual([['arm-loss:sniper:5', true]])
    expect(notifyCtor).not.toHaveBeenCalled()
    expect(document.title).toBe('CryptoBot')
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it('une TRANSITION après l\'amorçage notifie une fois et met le compteur dans le titre', () => {
    renderHook(() => useAlertMonitor())
    act(() =>
      useStore.setState({
        state: { bot_online: true, arms: [armLoss, { ...losingArm(-300), name: 'runner' } as Arm] },
        lastFrameAt: Date.now(),
      }),
    )
    expect(useAlertStore.getState().alerts.map((a) => a.key)).toEqual(['arm-loss:runner:5', 'arm-loss:sniper:5'])
    expect(notifyCtor).toHaveBeenCalledTimes(1)
    expect(document.title).toBe('(1) CryptoBot')
  })

  it('ne répète pas quand l\'état est repoussé à l\'identique', () => {
    renderHook(() => useAlertMonitor())
    act(() => useStore.setState({ state: { ...useStore.getState().state }, lastFrameAt: Date.now() }))
    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(notifyCtor).not.toHaveBeenCalled()
  })

  it('rechargement : la mémoire de session évite de relister l\'existant', () => {
    const first = renderHook(() => useAlertMonitor())
    first.unmount()
    useAlertStore.setState({ alerts: [] })
    renderHook(() => useAlertMonitor())
    expect(useAlertStore.getState().alerts).toEqual([])
    expect(notifyCtor).not.toHaveBeenCalled()
  })

  it('désactivé : aucune alerte, titre intact', () => {
    useAlertStore.setState({ settings: { enabled: false, lossPct: 5, sound: false } })
    renderHook(() => useAlertMonitor())
    expect(useAlertStore.getState().alerts).toEqual([])
    expect(document.title).toBe('CryptoBot')
  })

  it('aucune trame reçue : jamais de faux « Bot arrêté » à l\'ouverture', () => {
    useStore.setState({ lastFrameAt: null, state: { bot_online: false } })
    renderHook(() => useAlertMonitor())
    expect(useAlertStore.getState().alerts).toEqual([])
    expect(notifyCtor).not.toHaveBeenCalled()
  })

  it('bot qui s\'arrête APRÈS amorçage : alerte et notification', () => {
    renderHook(() => useAlertMonitor())
    act(() => useStore.setState({ state: { bot_online: false, arms: [armLoss] }, lastFrameAt: Date.now() }))
    expect(useAlertStore.getState().alerts.map((a) => a.key)).toContain('bot-stopped')
    expect(notifyCtor).toHaveBeenCalledTimes(1)
  })
})
