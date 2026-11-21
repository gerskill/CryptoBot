import { isNearStop } from './positionSignals'
import type { Arm, BotState, Position } from './types'

/** Miroir de `api/server.py` : STALE_AFTER_SECONDS et PREDICTION_STALE_SECONDS. */
export const BOT_STALE_SECONDS = 180
export const LIQUID_STALE_SECONDS = 600

export type AlertKind = 'arm-loss' | 'near-stop' | 'bot-stopped' | 'liquid-stopped'
export type AlertCandidate = { key: string; kind: AlertKind; title: string; detail: string }
export type AlertEntry = AlertCandidate & { id: string; at: number; read: boolean }

export type AlertSettings = { enabled: boolean; lossPct: number; sound: boolean }
export type AlertSnapshot = {
  arms: readonly Arm[]
  positions: readonly Position[]
  botStopped: boolean
  liquidStopped: boolean
}
export type LiquidHealth = { demo: boolean; runnerOnline: boolean; updatedAtIso: string | null }

/**
 * Le WebSocket ne pousse que lorsque `updated_at` change : un bot mort ne
 * produit AUCUNE trame, donc `bot_online` reste vrai côté client. On compare
 * donc l'âge de l'état à l'horloge locale. API injoignable = état inconnu
 * (déjà signalé ailleurs), pas « bot arrêté ».
 */
export function isBotStopped(
  state: Pick<BotState, 'bot_online' | 'updated_at'>,
  connected: boolean,
  nowMs: number,
): boolean {
  if (!connected) return false
  if (!state.bot_online) return true
  return state.updated_at != null && nowMs / 1000 - state.updated_at > BOT_STALE_SECONDS
}

/** Pas d'alerte sans réponse, en mode exemple, ou si la boucle n'a jamais publié. */
export function isLiquidStopped(health: LiquidHealth | null, nowMs: number): boolean {
  if (!health || health.demo || !health.updatedAtIso) return false
  if (!health.runnerOnline) return true
  const published = Date.parse(health.updatedAtIso)
  return !Number.isNaN(published) && (nowMs - published) / 1000 > LIQUID_STALE_SECONDS
}

function armLoss(arm: Arm, lossPct: number): AlertCandidate | null {
  const baseline = arm.stats?.baseline ?? 0
  const pnl = arm.stats?.total_pnl_usd ?? 0
  if (baseline <= 0) return null
  const pnlPct = (100 * pnl) / baseline
  if (pnlPct > -lossPct) return null
  return {
    key: `arm-loss:${arm.name}`,
    kind: 'arm-loss',
    title: `Bras ${arm.name} en perte`,
    detail: `${pnlPct.toFixed(1)} % de la mise de départ (seuil -${lossPct} %)`,
  }
}

function nearStop(position: Position): AlertCandidate | null {
  if (!isNearStop(position)) return null
  return {
    key: `near-stop:${position.id}`,
    kind: 'near-stop',
    title: `${position.symbol} proche du stop`,
    detail: `${position.arm ?? 'bras ?'} : ${position.pnl_pct.toFixed(1)} % pour un stop à ${position.stop_loss_pct.toFixed(1)} %`,
  }
}

/** État courant -> alertes en cours (sans mémoire : voir `dedupeAlerts`). */
export function detectAlerts(snapshot: AlertSnapshot, settings: AlertSettings): AlertCandidate[] {
  const found: (AlertCandidate | null)[] = [
    ...snapshot.arms.map((arm) => armLoss(arm, settings.lossPct)),
    ...snapshot.positions.map(nearStop),
    snapshot.botStopped
      ? {
          key: 'bot-stopped',
          kind: 'bot-stopped',
          title: 'Bot arrêté',
          detail: `Aucun signe de vie depuis plus de ${BOT_STALE_SECONDS} s.`,
        }
      : null,
    snapshot.liquidStopped
      ? {
          key: 'liquid-stopped',
          kind: 'liquid-stopped',
          title: 'Boucle Liquid arrêtée',
          detail: `Aucune publication depuis plus de ${LIQUID_STALE_SECONDS} s.`,
        }
      : null,
  ]
  return found.filter((alert): alert is AlertCandidate => alert !== null)
}

export type DedupeMemory = { lastSeen: Record<string, number>; lastNotifiedAt: number }
export type DedupeConfig = { windowMs: number; minGapMs: number }
export type DedupeResult = { fresh: AlertCandidate[]; toNotify: AlertCandidate[]; memory: DedupeMemory }

/**
 * Anti-spam. `fresh` : alertes à ajouter au centre (une par clé et par
 * fenêtre). `toNotify` : celles qui méritent en plus une notification système,
 * seulement si la précédente date de plus de `minGapMs`. La mémoire retournée
 * est une copie : l'appelant la conserve.
 */
export function dedupeAlerts(
  candidates: readonly AlertCandidate[],
  memory: DedupeMemory,
  nowMs: number,
  config: DedupeConfig,
): DedupeResult {
  const recent = Object.entries(memory.lastSeen).filter(([, at]) => nowMs - at < config.windowMs)
  const lastSeen: Record<string, number> = Object.fromEntries(recent)
  const fresh = candidates.filter((candidate) => !(candidate.key in lastSeen))
  const withNew = { ...lastSeen, ...Object.fromEntries(fresh.map((c) => [c.key, nowMs])) }
  const mayNotify = fresh.length > 0 && nowMs - memory.lastNotifiedAt >= config.minGapMs
  return {
    fresh,
    toNotify: mayNotify ? fresh : [],
    memory: { lastSeen: withNew, lastNotifiedAt: mayNotify ? nowMs : memory.lastNotifiedAt },
  }
}

/** Plusieurs alertes simultanées : une seule notification récapitulative. */
export function summarizeBatch(batch: readonly AlertCandidate[]): { title: string; body: string; tag: string } {
  if (batch.length === 1) return { title: batch[0].title, body: batch[0].detail, tag: batch[0].key }
  return {
    title: `${batch.length} alertes CryptoBot`,
    body: batch.map((alert) => alert.title).join(' · '),
    tag: 'cryptobot-batch',
  }
}
