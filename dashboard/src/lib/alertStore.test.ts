import { beforeEach, describe, expect, it } from 'vitest'
import { selectUnread, useAlertStore } from './alertStore'

const cand = (key: string) => ({ key, kind: 'bot-stopped' as const, title: key, detail: '' })

beforeEach(() => {
  localStorage.clear()
  useAlertStore.setState({ alerts: [] })
})

describe('alertStore', () => {
  it('ajoute les alertes les plus récentes en tête, non lues', () => {
    useAlertStore.getState().addAlerts([cand('a')], 1)
    useAlertStore.getState().addAlerts([cand('b')], 2)
    const { alerts } = useAlertStore.getState()
    expect(alerts.map((a) => a.key)).toEqual(['b', 'a'])
    expect(selectUnread(useAlertStore.getState())).toBe(2)
  })
  it('marque une alerte puis toutes comme lues', () => {
    useAlertStore.getState().addAlerts([cand('a'), cand('b')], 1)
    const first = useAlertStore.getState().alerts[0]
    useAlertStore.getState().markRead(first.id)
    expect(selectUnread(useAlertStore.getState())).toBe(1)
    useAlertStore.getState().markAllRead()
    expect(selectUnread(useAlertStore.getState())).toBe(0)
  })
  it('borne la liste à 50 alertes', () => {
    useAlertStore.getState().addAlerts(Array.from({ length: 60 }, (_, i) => cand(`k${i}`)), 1)
    expect(useAlertStore.getState().alerts).toHaveLength(50)
  })
  it('mémorise les réglages', () => {
    useAlertStore.getState().setSettings({ lossPct: 9 })
    expect(JSON.parse(localStorage.getItem('cryptobot.alerts.v1') ?? '{}').lossPct).toBe(9)
  })
})
