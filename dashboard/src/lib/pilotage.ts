/**
 * Pilotage depuis le site (ADR 021). Logique pure, sans React.
 *
 * Le site ne contourne rien : `stop` et `resume` passent par
 * `POST /api/bot/commandes` (même boîte que l'iPhone et Telegram), les bras
 * Liquid par `POST /api/prediction/requests` (`liquidRequests.ts`). Le serveur
 * valide ; ce module ne fait que formuler l'envoi et expliquer la réponse.
 */
import { sendRequest } from '../components/liquid/liquidRequests'

export type BotAction = 'stop' | 'resume'
export type Outcome = { ok: boolean; detail: string }
export type JournalEntry = {
  id: string
  /** Heure d'envoi, en millisecondes. */
  t: number
  label: string
  kind: 'bot' | 'liquid'
  ok: boolean
  detail: string
}

export const BOT_ACTIONS: readonly BotAction[] = ['stop', 'resume']
/** Au-delà, la boucle ignore une commande non lue (`src/core/commandes.py::EXPIRE_S`). */
export const EXPIRE_MS = 15 * 60 * 1000
export const JOURNAL_MAX = 20
const DETAIL_MAX = 300

/** Quoi, pourquoi, que faire : une phrase par morceau, jamais un code HTTP seul. */
export function describeFailure(status: number | null, detail?: string): string {
  const serveur = detail ? ` Serveur : ${detail}.` : ''
  if (status === null) {
    return 'Commande non envoyée : l’API du dashboard est injoignable. Vérifiez qu’elle tourne (uvicorn, port 8000) puis réessayer.'
  }
  if (status === 401) {
    return 'Commande refusée : jeton requis ou invalide (DASHBOARD_TOKEN). Saisissez le jeton du site puis réessayez.'
  }
  if (status === 400) {
    return `Commande refusée : requête incomplète.${serveur} Rechargez la page puis réessayez.`
  }
  if (status === 422) {
    return `Commande refusée par le serveur.${serveur} Rien n’a été transmis à la boucle.`
  }
  if (status === 429) {
    return `Commande refusée : trop de demandes en attente.${serveur} Vérifiez que la simulation tourne, puis réessayez.`
  }
  return `Commande non transmise : erreur serveur (HTTP ${status}).${serveur} Réessayez dans un instant.`
}

/** Envoie `stop` ou `resume`. Ne lève jamais : toute issue est un `Outcome`. */
export async function sendBotCommand(action: BotAction, token: string): Promise<Outcome> {
  if (!BOT_ACTIONS.includes(action)) return { ok: false, detail: 'Commande inconnue : rien n’a été envoyé.' }
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-CryptobBot': '1' }
  if (token) headers.Authorization = `Bearer ${token}`
  try {
    const res = await fetch('/api/bot/commandes', { method: 'POST', headers, body: JSON.stringify({ action }) })
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown; note?: unknown }
    if (res.ok) return { ok: true, detail: typeof body.note === 'string' ? body.note : 'Commande transmise à la boucle.' }
    return { ok: false, detail: describeFailure(res.status, typeof body.detail === 'string' ? body.detail : undefined) }
  } catch {
    return { ok: false, detail: describeFailure(null) }
  }
}

/** Demande Liquid (activer, désactiver, fork) : le serveur valide, on traduit son verdict. */
export async function sendLiquidAction(body: Record<string, unknown>, token: string): Promise<Outcome> {
  const error = await sendRequest(body, token)
  return error
    ? { ok: false, detail: `Refusée : ${error}` }
    : { ok: true, detail: 'Demande déposée, appliquée au prochain cycle de la simulation.' }
}

/** Nouvelle liste avec l'entrée en tête, bornée ; l'originale n'est jamais modifiée. */
export function appendEntry(list: readonly JournalEntry[], entry: JournalEntry): JournalEntry[] {
  return [entry, ...list].slice(0, JOURNAL_MAX)
}

function isEntry(value: unknown): value is JournalEntry {
  if (typeof value !== 'object' || value === null) return false
  const e = value as Record<string, unknown>
  return typeof e.id === 'string' && typeof e.t === 'number' && typeof e.label === 'string'
    && (e.kind === 'bot' || e.kind === 'liquid') && typeof e.ok === 'boolean' && typeof e.detail === 'string'
}

/** Relit le journal du navigateur : tout ce qui est mal formé est ignoré. */
export function parseJournal(raw: string | null): JournalEntry[] {
  if (!raw) return []
  try {
    const data: unknown = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    return data.filter(isEntry).slice(0, JOURNAL_MAX).map((e) => ({ ...e, detail: e.detail.slice(0, DETAIL_MAX) }))
  } catch {
    return []
  }
}

export type EntryStatus = { tone: 'refusee' | 'transmise' | 'perimee' | 'deposee'; text: string }

/** Statut lisible d'une entrée. Une commande du bot jamais confirmée après 15 min est périmée côté boucle. */
export function entryStatus(entry: JournalEntry, now: number): EntryStatus {
  if (!entry.ok) return { tone: 'refusee', text: 'refusée' }
  if (entry.kind === 'liquid') return { tone: 'deposee', text: 'déposée, appliquée au prochain cycle' }
  if (now - entry.t > EXPIRE_MS) {
    return { tone: 'perimee', text: 'périmée si la boucle ne l’a pas lue en 15 min : renvoyez-la si besoin' }
  }
  return { tone: 'transmise', text: 'transmise à la boucle' }
}
