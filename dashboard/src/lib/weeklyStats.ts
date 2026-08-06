/**
 * Fonctions pures de calcul du rapport hebdomadaire.
 *
 * Aucun import React, aucun appel store : tout part de `trades` en entrée et
 * renvoie une valeur nouvelle, testable isolément.
 */
import type { Trade } from './types'

export type ArmWeeklyStat = {
  arm: string
  trades: number
  wins: number
  winRate: number
  /** `Infinity` si aucune perte et au moins un gain — affichage à la charge du composant. */
  profitFactor: number
  pnlUsd: number
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000

/** Lundi 00:00:00 (heure locale) de la semaine contenant `date`. */
function startOfIsoWeek(date: Date): Date {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const day = d.getDay() // 0 = dimanche
  const diffToMonday = day === 0 ? -6 : 1 - day
  d.setDate(d.getDate() + diffToMonday)
  d.setHours(0, 0, 0, 0)
  return d
}

/** Bornes [lundi 00h00, dimanche 23h59:59.999] pour un décalage de semaines
 *  par rapport à la semaine courante (0 = cette semaine, -1 = la précédente). */
export function getWeekRange(weekOffset: number, now: Date = new Date()): { start: Date; end: Date } {
  const currentMonday = startOfIsoWeek(now)
  const start = new Date(currentMonday.getTime() + weekOffset * WEEK_MS)
  const end = new Date(start.getTime() + WEEK_MS - 1)
  return { start, end }
}

const DAYS_FR = ['dim', 'lun', 'mar', 'mer', 'jeu', 'ven', 'sam']
const MONTHS_FR = [
  'janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
  'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.',
]

/** « Cette semaine (lun 28 juil → dim 3 août) ». */
export function formatWeekLabel(weekOffset: number, now: Date = new Date()): string {
  const { start, end } = getWeekRange(weekOffset, now)
  const fmt = (d: Date) => `${DAYS_FR[d.getDay()]} ${d.getDate()} ${MONTHS_FR[d.getMonth()]}`
  const prefix = weekOffset === 0 ? 'Cette semaine' : weekOffset === -1 ? 'Semaine dernière' : `Semaine de`
  return `${prefix} (${fmt(start)} → ${fmt(end)})`
}

/** Filtre les trades d'une semaine ISO (lundi 00h00 → dimanche 23h59). */
export function filterTradesByWeek(trades: Trade[], weekOffset: number, now: Date = new Date()): Trade[] {
  const { start, end } = getWeekRange(weekOffset, now)
  const startMs = start.getTime()
  const endMs = end.getTime()
  return trades.filter((trade) => {
    const exitMs = new Date(trade.timestamp_exit).getTime()
    if (Number.isNaN(exitMs)) return false
    return exitMs >= startMs && exitMs <= endMs
  })
}

/** Regroupe par bras et calcule WR, PF, P&L, nombre de trades. */
export function computeArmStats(trades: Trade[]): ArmWeeklyStat[] {
  const byArm = new Map<string, Trade[]>()
  for (const trade of trades) {
    const key = trade.arm ?? 'inconnu'
    const list = byArm.get(key)
    if (list) list.push(trade)
    else byArm.set(key, [trade])
  }

  return Array.from(byArm.entries()).map(([arm, list]) => {
    const wins = list.filter((t) => t.pnl_usd > 0)
    const losses = list.filter((t) => t.pnl_usd < 0)
    const grossWin = wins.reduce((sum, t) => sum + t.pnl_usd, 0)
    const grossLoss = Math.abs(losses.reduce((sum, t) => sum + t.pnl_usd, 0))
    return {
      arm,
      trades: list.length,
      wins: wins.length,
      winRate: list.length ? (100 * wins.length) / list.length : 0,
      profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
      pnlUsd: list.reduce((sum, t) => sum + t.pnl_usd, 0),
    }
  })
}

/** Reconstruit une mini equity_series depuis les trades filtrés, triés par
 *  `timestamp_exit`. Ne contient PAS le point de départ : à préfixer par
 *  l'appelant s'il veut afficher la mise initiale. */
export function buildWeeklyEquitySeries(trades: Trade[], startingEquity: number): number[] {
  const sorted = [...trades].sort(
    (a, b) => new Date(a.timestamp_exit).getTime() - new Date(b.timestamp_exit).getTime(),
  )
  let running = startingEquity
  return sorted.map((trade) => (running += trade.pnl_usd))
}
