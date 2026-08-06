/**
 * Calcul du drawdown depuis une série de P&L delta par trade. Fonction pure,
 * aucune dépendance React : testable isolément.
 *
 * Exemple de référence : `[10, -20, 5]` depuis un capital de 1000 donne un pic
 * à 1010 (après le premier trade), puis un creux à 990 → drawdown ≈ 1.98 %.
 */
export function computeDrawdownSeries(series: number[], startingEquity: number): number[] {
  let running = startingEquity
  let peak = startingEquity
  const drawdowns: number[] = []

  for (const delta of series) {
    running += delta
    peak = Math.max(peak, running)
    const drawdownPct = peak > 0 ? (100 * (peak - running)) / peak : 0
    drawdowns.push(drawdownPct)
  }

  return drawdowns
}

/** Drawdown % maximal atteint sur toute la série. 0 si la série est vide. */
export function computeMaxDrawdown(series: number[], startingEquity: number): number {
  const drawdowns = computeDrawdownSeries(series, startingEquity)
  return drawdowns.length ? Math.max(...drawdowns) : 0
}
