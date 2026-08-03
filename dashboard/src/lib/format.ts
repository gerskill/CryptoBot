/** Formatage orienté trader : des « x », pas des décimales inutiles. */

export const usd = (value: number, digits = 0) =>
  `$${value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`

/** Un prix de memecoin descend à 1e-9 : notation adaptative. */
export const price = (value: number) => {
  if (!value) return '—'
  if (value >= 1) return `$${value.toFixed(4)}`
  if (value >= 0.0001) return `$${value.toFixed(6)}`
  return `$${value.toFixed(9)}`
}

export const pct = (value: number, digits = 1) =>
  `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`

/** Le trader lit « 2.4x », pas « +140% ». */
export const multiple = (pnlPct: number) => `${(1 + pnlPct / 100).toFixed(2)}x`

export const compact = (value: number) => {
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`
  return value.toFixed(0)
}

export const age = (hours: number) => {
  const minutes = Math.round(hours * 60)
  if (minutes < 60) return `${minutes} min`
  return `${hours.toFixed(1)} h`
}

export const duration = (minutes: number) => {
  if (minutes < 60) return `${Math.round(minutes)} min`
  return `${(minutes / 60).toFixed(1)} h`
}

/** Vert au-dessus de zéro, rouge en dessous. Utilisé partout. */
export const pnlColor = (value: number) =>
  value > 0 ? 'text-toxic' : value < 0 ? 'text-blood' : 'text-dim'

/** « il y a 2h », « à l'instant » — pour une timeline qui ne recalcule pas
 *  un horodatage complet à chaque ligne. */
export const relativeTime = (isoTimestamp: string): string => {
  const then = new Date(isoTimestamp).getTime()
  if (Number.isNaN(then)) return isoTimestamp
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60000))
  if (minutes < 1) return "à l'instant"
  if (minutes < 60) return `il y a ${minutes} min`
  const hours = minutes / 60
  if (hours < 24) return `il y a ${hours.toFixed(hours < 10 ? 1 : 0)}h`
  return `il y a ${Math.round(hours / 24)} j`
}
