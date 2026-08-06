import { useEffect, useRef, useState } from 'react'

export type ArmParamsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; params: Record<string, unknown>; history: unknown[] }
  | { status: 'unauthorized' }
  | { status: 'error'; message: string }

type CacheEntry = { status: 'ok'; params: Record<string, unknown>; history: unknown[] }

/**
 * Fetch de `/api/params?arm=...` avec cache en mémoire (par bras + token) et
 * annulation propre : changer de bras avant la fin d'un fetch précédent ne
 * doit jamais laisser une réponse tardive écraser l'état courant.
 */
export function useArmParams(arm: string | null, token: string): ArmParamsState {
  const cache = useRef(new Map<string, CacheEntry>())
  const [state, setState] = useState<ArmParamsState>({ status: 'idle' })

  useEffect(() => {
    if (!arm) {
      setState({ status: 'idle' })
      return
    }

    const cacheKey = `${arm}::${token}`
    const cached = cache.current.get(cacheKey)
    if (cached) {
      setState(cached)
      return
    }

    const controller = new AbortController()
    setState({ status: 'loading' })

    const query = new URLSearchParams({ arm })
    if (token) query.set('token', token)

    fetch(`/api/params?${query.toString()}`, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 401) {
          setState({ status: 'unauthorized' })
          return
        }
        if (!response.ok) {
          setState({ status: 'error', message: `Le serveur a répondu ${response.status}.` })
          return
        }
        const data = await response.json()
        const result: CacheEntry = {
          status: 'ok',
          params: (data.params ?? {}) as Record<string, unknown>,
          history: (data.history ?? []) as unknown[],
        }
        cache.current.set(cacheKey, result)
        setState(result)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        const message = error instanceof Error ? error.message : 'Erreur réseau inconnue.'
        setState({ status: 'error', message })
      })

    return () => controller.abort()
  }, [arm, token])

  return state
}
