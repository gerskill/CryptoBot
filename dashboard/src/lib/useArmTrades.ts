import { useEffect, useRef, useState } from 'react'
import { useStore } from './store'

export type ArmTradesResult = {
  arm: string
  trades: { timestamp_exit: string; pnl_usd: number }[]
  status: 'loading' | 'ok' | 'error'
}

/**
 * TOUS les trades clôturés de chaque bras demandé, via `/api/trades/range`
 * (sans bornes : jamais la fenêtre de 100 de `/api/trades`). La plage est
 * appliquée côté client (`dateRange.ts`) : changer de plage ne refait aucun
 * appel. Cache par bras, vidé quand la flotte clôture une position.
 */
export function useArmTrades(armNames: readonly string[]): ArmTradesResult[] {
  const key = armNames.join(',')
  const closedCount = useStore((s) => s.state.aggregate?.closed_trades ?? s.state.stats?.total_trades ?? 0)
  const cache = useRef({ closedCount, byArm: new Map<string, ArmTradesResult['trades']>() })
  const [results, setResults] = useState<ArmTradesResult[]>([])

  useEffect(() => {
    if (cache.current.closedCount !== closedCount) cache.current = { closedCount, byArm: new Map() }
    const byArm = cache.current.byArm
    const names = key ? key.split(',') : []
    const controller = new AbortController()
    setResults(
      names.map((arm) => ({ arm, trades: byArm.get(arm) ?? [], status: byArm.has(arm) ? 'ok' : 'loading' })),
    )

    Promise.all(
      names.map(async (arm): Promise<ArmTradesResult> => {
        const cached = byArm.get(arm)
        if (cached) return { arm, trades: cached, status: 'ok' }
        try {
          const response = await fetch(`/api/trades/range?arm=${encodeURIComponent(arm)}`, {
            signal: controller.signal,
          })
          if (!response.ok) return { arm, trades: [], status: 'error' }
          const body = await response.json()
          const trades: ArmTradesResult['trades'] = body.trades ?? []
          byArm.set(arm, trades)
          return { arm, trades, status: 'ok' }
        } catch {
          return { arm, trades: [], status: 'error' }
        }
      }),
    ).then((settled) => {
      if (!controller.signal.aborted) setResults(settled)
    })
    return () => controller.abort()
  }, [key, closedCount])

  return results
}
