import { useEffect, useState } from 'react'
import { useStore } from './store'
import type { ArmsPerformanceResponse, PerformanceWindow } from './types'

export type ArmPerformanceState = {
  data: ArmsPerformanceResponse | null
  error: string | null
}

/**
 * `/api/arms/performance?window=…` — P&L par bras sur 7 j, 30 j ou tout.
 *
 * Calculé CÔTÉ SERVEUR, sur toutes les positions (`read_positions`), pas
 * filtré ici depuis le store : le store ne garde que les 100 dernières
 * positions, ce qui avait déjà faussé le rapport hebdo (+192 $ affichés pour
 * une semaine réelle d'environ -5 000 $).
 *
 * Rechargé quand la fenêtre change ou qu'une position de la flotte se ferme.
 */
export function useArmPerformance(periode: PerformanceWindow): ArmPerformanceState {
  const closedCount = useStore(
    (s) => s.state.aggregate?.closed_trades ?? s.state.stats?.total_trades ?? 0,
  )
  const [result, setResult] = useState<ArmPerformanceState & { window: PerformanceWindow | null }>({
    data: null,
    error: null,
    window: null,
  })

  useEffect(() => {
    const controller = new AbortController()
    fetch(`/api/arms/performance?window=${encodeURIComponent(periode)}`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((body: ArmsPerformanceResponse) => setResult({ data: body, error: null, window: periode }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setResult({ data: null, error: error instanceof Error ? error.message : String(error), window: periode })
      })
    return () => controller.abort()
  }, [periode, closedCount])

  // Pas d'anciens chiffres sous une nouvelle étiquette de fenêtre.
  if (result.window !== periode) return { data: null, error: null }
  return { data: result.data, error: result.error }
}
