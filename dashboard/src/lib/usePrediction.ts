import { useEffect, useState } from 'react'
import type { PredictionBet, PredictionSummary, PricePoint } from './predictionTypes'

const REFRESH_MS = 15_000

export type PredictionData = {
  summary: PredictionSummary | null
  bets: PredictionBet[]
  error: string | null
  /** Mode auquel appartiennent les données : évite d'afficher du fictif sous
   *  l'étiquette du réel pendant le rechargement. */
  demo: boolean | null
}

/** Résumé par bras + tous les paris. Rechargé toutes les 15 s. */
export function usePrediction(demo: boolean): PredictionData {
  const [data, setData] = useState<PredictionData>({ summary: null, bets: [], error: null, demo: null })

  useEffect(() => {
    const controller = new AbortController()
    const suffix = demo ? '?demo=true' : ''
    const load = () => {
      Promise.all([
        fetch(`/api/prediction/summary${suffix}`, { signal: controller.signal }),
        fetch(`/api/prediction/bets${suffix}`, { signal: controller.signal }),
      ])
        .then(async ([summaryRes, betsRes]) => {
          if (!summaryRes.ok) throw new Error(`HTTP ${summaryRes.status}`)
          if (!betsRes.ok) throw new Error(`HTTP ${betsRes.status}`)
          const summary = (await summaryRes.json()) as PredictionSummary
          const body = (await betsRes.json()) as { bets?: PredictionBet[] }
          setData({ summary, bets: body.bets ?? [], error: null, demo })
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return
          setData((prev) => ({
            ...prev,
            error: error instanceof Error ? error.message : String(error),
          }))
        })
    }
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => {
      controller.abort()
      clearInterval(id)
    }
  }, [demo])

  if (data.demo !== demo) return { summary: null, bets: [], error: data.error, demo: null }
  return data
}

export type PriceState = { coin: string | null; points: PricePoint[]; error: string | null }

/** Série de prix de la pièce d'un pari, chargée à la sélection. */
export function useBetPrices(coin: string | null, demo: boolean): PriceState {
  const [state, setState] = useState<PriceState>({ coin: null, points: [], error: null })

  useEffect(() => {
    if (!coin) return
    const controller = new AbortController()
    const params = new URLSearchParams({ coin })
    if (demo) params.set('demo', 'true')
    fetch(`/api/prediction/prices?${params.toString()}`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((body: { points?: PricePoint[] }) =>
        setState({ coin, points: body.points ?? [], error: null }),
      )
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({ coin, points: [], error: error instanceof Error ? error.message : String(error) })
      })
    return () => controller.abort()
  }, [coin, demo])

  if (state.coin !== coin) return { coin, points: [], error: null }
  return state
}
