import { useEffect, useRef, useState } from 'react'
import type { Arm } from './types'

export type ArmEquityResult = {
  arm: string
  /** `equity_series` brut : delta P&L par trade, chronologique, non borné. */
  series: number[]
  status: 'loading' | 'ok' | 'error'
}

/**
 * Fetch en parallèle de `/api/trades?arm=...` pour chaque bras activé.
 * Résultats mis en cache par bras dans un `useRef` : un changement de props
 * qui ne touche pas la liste de noms ne redéclenche jamais de fetch.
 */
export function useArmEquity(arms: Arm[]): ArmEquityResult[] {
  const armNames = arms.map((arm) => arm.name).join(',')
  const cache = useRef(new Map<string, number[]>())
  const [results, setResults] = useState<ArmEquityResult[]>([])

  useEffect(() => {
    const names = armNames ? armNames.split(',') : []
    if (names.length === 0) {
      setResults([])
      return
    }

    const controller = new AbortController()
    setResults(
      names.map((name) => ({
        arm: name,
        series: cache.current.get(name) ?? [],
        status: cache.current.has(name) ? 'ok' : 'loading',
      })),
    )

    Promise.all(
      names.map(async (name): Promise<ArmEquityResult> => {
        const cached = cache.current.get(name)
        if (cached) return { arm: name, series: cached, status: 'ok' }
        try {
          const response = await fetch(`/api/trades?arm=${encodeURIComponent(name)}`, {
            signal: controller.signal,
          })
          if (!response.ok) return { arm: name, series: [], status: 'error' }
          const data = await response.json()
          const series: number[] = data.equity_series ?? []
          cache.current.set(name, series)
          return { arm: name, series, status: 'ok' }
        } catch {
          return { arm: name, series: [], status: 'error' }
        }
      }),
    ).then((settled) => {
      if (!controller.signal.aborted) setResults(settled)
    })

    return () => controller.abort()
  }, [armNames])

  return results
}
