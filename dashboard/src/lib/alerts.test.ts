import { describe, expect, it } from 'vitest'
import {
  BOT_STALE_SECONDS,
  LIQUID_STALE_SECONDS,
  dedupeAlerts,
  detectAlerts,
  isBotStopped,
  isLiquidStopped,
  LIQUID_POLL_MS,
  primeAlerts,
  type LiquidHealth,
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
  const frame = (ageS: number, connectedAgeS = 9999) => ({
    lastFrameAt: NOW - ageS * 1000,
    connectedAt: NOW - connectedAgeS * 1000,
  })
  it('constantes miroir du serveur', () => {
    expect(BOT_STALE_SECONDS).toBe(180)
    expect(LIQUID_STALE_SECONDS).toBe(600)
  })
  it('aucune trame reçue (état initial bot_online:false) : inconnu, pas « arrêté »', () => {
    expect(isBotStopped({ bot_online: false }, true, { lastFrameAt: null, connectedAt: NOW }, NOW)).toBe(false)
  })
  it('API injoignable : inconnu', () => {
    expect(isBotStopped({ bot_online: false }, false, frame(1), NOW)).toBe(false)
  })
  it('trame réelle disant hors ligne : arrêté', () => {
    expect(isBotStopped({ bot_online: false }, true, frame(1), NOW)).toBe(true)
  })
  it('âge mesuré depuis la RÉCEPTION locale : 180 s', () => {
    expect(isBotStopped({ bot_online: true }, true, frame(179), NOW)).toBe(false)
    expect(isBotStopped({ bot_online: true }, true, frame(181), NOW)).toBe(true)
  })
  it('à la reconnexion, l\'âge repart de la connexion', () => {
    expect(isBotStopped({ bot_online: true }, true, frame(600, 5), NOW)).toBe(false)
    expect(isBotStopped({ bot_online: true }, true, frame(600, 200), NOW)).toBe(true)
  })
})

describe('isLiquidStopped', () => {
  const health = (over: Partial<LiquidHealth> = {}): LiquidHealth => ({
    demo: false,
    runnerOnline: true,
    updatedAtIso: '2026-01-01T00:00:00Z',
    fetchedAt: NOW - 1000,
    ...over,
  })
  it('rien de chargé, exemple fictif ou boucle jamais lancée : pas d\'alerte', () => {
    expect(isLiquidStopped(null, NOW)).toBe(false)
    expect(isLiquidStopped(health({ demo: true, runnerOnline: false }), NOW)).toBe(false)
    expect(isLiquidStopped(health({ runnerOnline: false, updatedAtIso: null }), NOW)).toBe(false)
  })
  it('se fie à runner_online du serveur', () => {
    expect(isLiquidStopped(health({ runnerOnline: false }), NOW)).toBe(true)
    expect(isLiquidStopped(health({ runnerOnline: true }), NOW)).toBe(false)
  })
  it('API injoignable (réponse vieille de plus de 2 sondages) : inconnu', () => {
    expect(isLiquidStopped(health({ runnerOnline: false, fetchedAt: NOW - 2 * LIQUID_POLL_MS - 1 }), NOW)).toBe(false)
  })
})

describe('detectAlerts', () => {
  it('rien à signaler quand tout va bien', () => {
    expect(detectAlerts({ ...quiet, arms: [arm('a', -10)], positions: [pos({ pnl_pct: -3 })] }, settings)).toEqual([])
  })
  it('bras sous le seuil de perte (en % de sa mise de départ)', () => {
    const out = detectAlerts({ ...quiet, arms: [arm('a', -50), arm('b', -49.9), arm('c', 20)] }, settings)
    expect(out.map((a) => a.key)).toEqual(['arm-loss:a:5'])
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

describe('primeAlerts', () => {
  const c = (key: string) => ({ key, kind: 'near-stop' as const, title: key, detail: '' })
  it('mémorise l\'existant sans toucher lastNotifiedAt, renvoie ce qui était inconnu', () => {
    const memory: DedupeMemory = { lastSeen: { known: 5 }, lastNotifiedAt: 7 }
    const r = primeAlerts([c('known'), c('new')], memory, NOW)
    expect(r.fresh.map((x) => x.key)).toEqual(['new'])
    expect(r.memory).toEqual({ lastSeen: { known: 5, new: NOW }, lastNotifiedAt: 7 })
    expect(memory.lastSeen).toEqual({ known: 5 })
  })
})

describe('clé de perte et seuil', () => {
  it('changer le seuil change la clé (nouvelle alerte si toujours franchi)', () => {
    const snap = { ...quiet, arms: [arm('a', -200)] }
    const k5 = detectAlerts(snap, settings)[0].key
    const k10 = detectAlerts(snap, { ...settings, lossPct: 10 })[0].key
    expect(k5).not.toBe(k10)
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
