/** Plages de dates pour la comparaison de bras. Fonctions pures. */

export type RangePreset = '7d' | '30d' | 'all' | 'custom'
export type RangeChoice = { preset: RangePreset; from?: string; to?: string }
export type ResolvedRange = { since: string | null; until: string | null }
type Dated = { timestamp_exit: string; pnl_usd: number }

const DAY_MS = 86_400_000
const PRESET_DAYS: Record<'7d' | '30d', number> = { '7d': 7, '30d': 30 }
const HAS_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i
const NAIVE_ISO = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/

/**
 * Instant d'un horodatage du journal. Le serveur lit un ISO SANS fuseau comme
 * de l'UTC (`_parse_ts`) ; `Date.parse` l'aurait lu en heure locale du
 * navigateur : décalage de 1 à 2 h sur les bornes de plage.
 */
export function parseUtc(value: string): number {
  const text = value.trim()
  return NAIVE_ISO.test(text) && !HAS_ZONE.test(text) ? Date.parse(`${text.replace(' ', 'T')}Z`) : Date.parse(text)
}

const DATE_INPUT = /^(\d{4})-(\d{2})-(\d{2})$/

/** `YYYY-MM-DD` (champ date) -> minuit LOCAL du navigateur, décalé de `plusDays`. */
function localMidnight(value: string | undefined, plusDays = 0): string | null {
  const match = value ? DATE_INPUT.exec(value) : null
  if (!match) return null
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + plusDays)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}

export function resolveRange(choice: RangeChoice, now: Date = new Date()): ResolvedRange {
  if (choice.preset === 'all') return { since: null, until: null }
  if (choice.preset === 'custom') {
    // Fin inclusive : « jusqu'au 3 » couvre toute la journée du 3.
    return { since: localMidnight(choice.from), until: localMidnight(choice.to, 1) }
  }
  return { since: new Date(now.getTime() - PRESET_DAYS[choice.preset] * DAY_MS).toISOString(), until: null }
}

/** Filtre sur la date de CLÔTURE, comme `/api/trades/range` : [since, until[. */
export function filterByRange<T extends Dated>(rows: readonly T[], range: ResolvedRange): T[] {
  const since = range.since ? Date.parse(range.since) : null
  const until = range.until ? Date.parse(range.until) : null
  return rows.filter((row) => {
    const at = parseUtc(row.timestamp_exit)
    if (Number.isNaN(at)) return false
    if (since !== null && at < since) return false
    if (until !== null && at >= until) return false
    return true
  })
}

/** P&L réalisé avant la borne basse : sert d'équité de départ de la plage. */
export function pnlBefore(rows: readonly Dated[], since: string | null): number {
  if (!since) return 0
  const limit = Date.parse(since)
  return rows.reduce((sum, row) => {
    const at = parseUtc(row.timestamp_exit)
    return !Number.isNaN(at) && at < limit ? sum + row.pnl_usd : sum
  }, 0)
}

/** Début >= fin : la plage ne contient rien, autant le dire plutôt que d'afficher du vide. */
export function isInvalidRange(range: ResolvedRange): boolean {
  return range.since !== null && range.until !== null && Date.parse(range.since) >= Date.parse(range.until)
}
