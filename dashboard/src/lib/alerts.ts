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
export const LIQUID_POLL_MS = 30_000
export type LiquidHealth = {
  demo: boolean
  /** `runner_online` : déjà calculé côté serveur (âge < 600 s), pas recalculé ici. */
  runnerOnline: boolean
  updatedAtIso: string | null
  /** Réception LOCALE de cette réponse : au-delà de 2 sondages, la donnée est périmée. */
  fetchedAt: number
}
export type BotFrame = { lastFrameAt: number | null; connectedAt: number | null }

/**
 * Le WebSocket ne pousse que lorsque `updated_at` change : un bot mort ne
 * produit AUCUNE trame, donc `bot_online` reste vrai côté client. On mesure
 * l'âge depuis la réception LOCALE de la dernière trame (jamais l'epoch
 * serveur contre l'horloge du navigateur), ou depuis la (re)connexion si elle
 * est plus récente. Sans trame reçue ou API injoignable : état inconnu, donc
 * pas d'alerte. `!bot_online` n'a de sens que sur une trame réelle.
 */
export function isBotStopped(
  state: Pick<BotState, 'bot_online'>,
  connected: boolean,
  frame: BotFrame,
  nowMs: number,
): boolean {
  if (!connected || frame.lastFrameAt === null) return false
  if (!state.bot_online) return true
  const reference = Math.max(frame.lastFrameAt, frame.connectedAt ?? 0)
  return (nowMs - reference) / 1000 > BOT_STALE_SECONDS
}

/**
 * Pas d'alerte sans réponse récente, en mode exemple, ou si la boucle n'a
 * jamais publié. API injoignable (réponse périmée) = inconnu, pas « arrêtée ».
 */
export function isLiquidStopped(health: LiquidHealth | null, nowMs: number): boolean {
  if (!health || health.demo || !health.updatedAtIso) return false
  if (nowMs - health.fetchedAt > 2 * LIQUID_POLL_MS) return false
  return !health.runnerOnline
}

function armLoss(arm: Arm, lossPct: number): AlertCandidate | null {
  const baseline = arm.stats?.baseline ?? 0
  const pnl = arm.stats?.total_pnl_usd ?? 0
  if (baseline <= 0) return null
  const pnlPct = (100 * pnl) / baseline
  if (pnlPct > -lossPct) return null
  return {
    key: `arm-loss:${arm.name}:${lossPct}`,
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

/**
 * Amorçage : l'état DÉJÀ existant à l'ouverture, au rechargement ou à
 * l'activation n'est pas une nouveauté. On le mémorise (sans écraser une
 * mémoire plus ancienne) et on renvoie ce qui n'était pas connu, à lister
 * comme « lu » sans notification ni bip.
 */
export function primeAlerts(
  candidates: readonly AlertCandidate[],
  memory: DedupeMemory,
  nowMs: number,
): { fresh: AlertCandidate[]; memory: DedupeMemory } {
  const fresh = candidates.filter((candidate) => !(candidate.key in memory.lastSeen))
  const added = Object.fromEntries(fresh.map((candidate) => [candidate.key, nowMs]))
  return { fresh, memory: { ...memory, lastSeen: { ...memory.lastSeen, ...added } } }
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
