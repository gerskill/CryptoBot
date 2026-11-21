import { describe, expect, it } from 'vitest'
import {
  BOT_STALE_SECONDS,
  LIQUID_STALE_SECONDS,
  dedupeAlerts,
  detectAlerts,
  isBotStopped,
  isLiquidStopped,
  summarizeBatch,
  type DedupeMemory,
} from './alerts'
import type { Arm, Position } from './types'

const NOW = 1_800_000_000_000

const arm = (name: string, pnl: number, baseline = 1000): Arm =>
  ({ name, enabled: true, stats: { baseline, total_pnl_usd: pnl } }) as unknown as Arm
const pos = (over: Partial<Position> = {}): Position =>
  ({ id: 'p1', arm: 'sniper', symbol: 'PEPE', pnl_pct: 0, stop_loss_pct: -20, take_profit_1: 50, ...over }) as Position
const quiet = { arms: [], positions: [], botStopped: false, liquidStopped: false }
const settings = { enabled: true, lossPct: 5, sound: false }

describe('isBotStopped', () => {
  it('constantes miroir du serveur', () => {
    expect(BOT_STALE_SECONDS).toBe(180)
    expect(LIQUID_STALE_SECONDS).toBe(600)
  })
  it('API injoignable : inconnu, pas « arrêté »', () => {
    expect(isBotStopped({ bot_online: false }, false, NOW)).toBe(false)
  })
  it('bot hors ligne selon le serveur', () => {
    expect(isBotStopped({ bot_online: false }, true, NOW)).toBe(true)
  })
  it('état figé : plus de poussée au-delà de 180 s', () => {
    const fresh = { bot_online: true, updated_at: NOW / 1000 - 179 }
    const stale = { bot_online: true, updated_at: NOW / 1000 - 181 }
    expect(isBotStopped(fresh, true, NOW)).toBe(false)
    expect(isBotStopped(stale, true, NOW)).toBe(true)
  })
  it('sans horodatage, se fie au serveur', () => {
    expect(isBotStopped({ bot_online: true }, true, NOW)).toBe(false)
  })
})

describe('isLiquidStopped', () => {
  it('rien de chargé, exemple fictif ou boucle jamais lancée : pas d\'alerte', () => {
    expect(isLiquidStopped(null, NOW)).toBe(false)
    expect(isLiquidStopped({ demo: true, runnerOnline: false, updatedAtIso: '2026-01-01T00:00:00Z' }, NOW)).toBe(false)
    expect(isLiquidStopped({ demo: false, runnerOnline: false, updatedAtIso: null }, NOW)).toBe(false)
  })
  it('arrêtée si le serveur le dit ou au-delà de 600 s', () => {
    const iso = (s: number) => new Date(NOW - s * 1000).toISOString()
    expect(isLiquidStopped({ demo: false, runnerOnline: false, updatedAtIso: iso(10) }, NOW)).toBe(true)
    expect(isLiquidStopped({ demo: false, runnerOnline: true, updatedAtIso: iso(601) }, NOW)).toBe(true)
    expect(isLiquidStopped({ demo: false, runnerOnline: true, updatedAtIso: iso(599) }, NOW)).toBe(false)
  })
})

describe('detectAlerts', () => {
  it('rien à signaler quand tout va bien', () => {
    expect(detectAlerts({ ...quiet, arms: [arm('a', -10)], positions: [pos({ pnl_pct: -3 })] }, settings)).toEqual([])
  })
  it('bras sous le seuil de perte (en % de sa mise de départ)', () => {
    const out = detectAlerts({ ...quiet, arms: [arm('a', -50), arm('b', -49.9), arm('c', 20)] }, settings)
    expect(out.map((a) => a.key)).toEqual(['arm-loss:a'])
    expect(out[0].kind).toBe('arm-loss')
  })
  it('seuil de perte paramétrable', () => {
    const out = detectAlerts({ ...quiet, arms: [arm('a', -20)] }, { ...settings, lossPct: 1 })
    expect(out).toHaveLength(1)
  })
  it('bras sans mise de départ : ignoré', () => {
    expect(detectAlerts({ ...quiet, arms: [arm('a', -500, 0)] }, settings)).toEqual([])
  })
  it('position proche du stop réel', () => {
    const out = detectAlerts({ ...quiet, positions: [pos({ pnl_pct: -12 })] }, settings)
    expect(out.map((a) => a.key)).toEqual(['near-stop:p1'])
    expect(out[0].title).toContain('PEPE')
  })
  it('bot et boucle Liquid arrêtés', () => {
    const out = detectAlerts({ ...quiet, botStopped: true, liquidStopped: true }, settings)
    expect(out.map((a) => a.key)).toEqual(['bot-stopped', 'liquid-stopped'])
  })
})

describe('dedupeAlerts', () => {
  const cfg = { windowMs: 30 * 60_000, minGapMs: 60_000 }
  const empty: DedupeMemory = { lastSeen: {}, lastNotifiedAt: 0 }
  const a = { key: 'bot-stopped', kind: 'bot-stopped' as const, title: 't', detail: 'd' }
  const b = { key: 'arm-loss:x', kind: 'arm-loss' as const, title: 't2', detail: 'd2' }

  it('une première occurrence est nouvelle et notifiable', () => {
    const r = dedupeAlerts([a], empty, NOW, cfg)
    expect(r.fresh).toEqual([a])
    expect(r.toNotify).toEqual([a])
  })
  it('la même alerte ne revient pas dans la fenêtre, puis revient après', () => {
    const first = dedupeAlerts([a], empty, NOW, cfg)
    expect(dedupeAlerts([a], first.memory, NOW + 5 * 60_000, cfg).fresh).toEqual([])
    expect(dedupeAlerts([a], first.memory, NOW + 31 * 60_000, cfg).fresh).toEqual([a])
  })
  it('espacement minimal : alerte listée mais sans notification système', () => {
    const first = dedupeAlerts([a], empty, NOW, cfg)
    const second = dedupeAlerts([b], first.memory, NOW + 10_000, cfg)
    expect(second.fresh).toEqual([b])
    expect(second.toNotify).toEqual([])
    expect(dedupeAlerts([b], first.memory, NOW + 61_000, cfg).toNotify).toEqual([b])
  })
  it('ne mute pas la mémoire reçue', () => {
    const memory: DedupeMemory = { lastSeen: {}, lastNotifiedAt: 0 }
    dedupeAlerts([a], memory, NOW, cfg)
    expect(memory).toEqual({ lastSeen: {}, lastNotifiedAt: 0 })
  })
  it('oublie les clés plus vieilles que la fenêtre', () => {
    const memory: DedupeMemory = { lastSeen: { old: NOW - 40 * 60_000 }, lastNotifiedAt: 0 }
    expect(dedupeAlerts([], memory, NOW, cfg).memory.lastSeen).toEqual({})
  })
})

describe('summarizeBatch', () => {
  it('une alerte : titre et détail tels quels', () => {
    const r = summarizeBatch([{ key: 'k', kind: 'bot-stopped', title: 'Bot arrêté', detail: 'x' }])
    expect(r).toEqual({ title: 'Bot arrêté', body: 'x', tag: 'k' })
  })
  it('plusieurs alertes : un seul message récapitulatif', () => {
    const r = summarizeBatch([
      { key: 'a', kind: 'bot-stopped', title: 'A', detail: 'da' },
      { key: 'b', kind: 'arm-loss', title: 'B', detail: 'db' },
    ])
    expect(r.title).toBe('2 alertes CryptoBot')
    expect(r.body).toContain('A')
    expect(r.body).toContain('B')
  })
})
