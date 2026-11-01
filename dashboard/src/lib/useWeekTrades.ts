import { useEffect, useState } from 'react'
import { useStore } from './store'
import { getWeekRange } from './weeklyStats'
import type { Trade } from './types'

export type WeekTradesState = {
  trades: Trade[] | null
  loading: boolean
  error: string | null
}

/**
 * Toutes les positions clôturées d'une semaine (tous bras), via
 * `/api/trades/range`.
 *
 * LE BUG CORRIGÉ. Le rapport hebdo filtrait la liste du store, qui ne contient
 * que les 100 dernières positions (`/api/trades`, tableau « derniers trades »).
 * Avec ~12 bras, 100 positions ≈ 13 h : « cette semaine » affichait +192 $
 * au lieu d'environ -5 000 $. Ici on demande la semaine entière au serveur.
 *
 * Bornes : lundi 00:00 → lundi suivant 00:00 en HEURE LOCALE du navigateur
 * (Europe/Zurich sur le Mac), envoyées en ISO UTC — le serveur compare des
 * instants absolus, donc aucun décalage de 2 h.
 *
 * Rechargé quand la semaine change ou quand le nombre de positions clôturées
 * de la flotte change (même déclencheur que `useLiveState`).
 */
export function useWeekTrades(weekOffset: number): WeekTradesState {
  const closedCount = useStore(
    (s) => s.state.aggregate?.closed_trades ?? s.state.stats?.total_trades ?? 0,
  )
  // `offset` mémorise la semaine à laquelle appartiennent les données : au
  // changement de semaine on n'affiche pas l'ancienne le temps du fetch.
  const [result, setResult] = useState<WeekTradesState & { offset: number | null }>({
    trades: null,
    loading: true,
    error: null,
    offset: null,
  })

  useEffect(() => {
    const controller = new AbortController()
    const { start } = getWeekRange(weekOffset)
    const { start: nextStart } = getWeekRange(weekOffset + 1)
    const params = new URLSearchParams({
      arm: 'all',
      since: start.toISOString(),
      until: nextStart.toISOString(),
    })
    fetch(`/api/trades/range?${params.toString()}`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((body) =>
        setResult({ trades: body.trades ?? [], loading: false, error: null, offset: weekOffset }),
      )
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setResult((prev) => ({
          trades: prev.offset === weekOffset ? prev.trades : null,
          loading: false,
          error: error instanceof Error ? error.message : String(error),
          offset: prev.offset === weekOffset ? prev.offset : null,
        }))
      })
    return () => controller.abort()
  }, [weekOffset, closedCount])

  if (result.offset !== weekOffset) {
    return { trades: null, loading: result.error === null, error: result.error }
  }
  return { trades: result.trades, loading: result.loading, error: result.error }
}
