import { useEffect, useRef } from 'react'
import { useStore } from './store'

const RECONNECT_DELAY_MS = 2000

/**
 * Connexion temps réel à l'API.
 *
 * Le WebSocket ne pousse que lorsque `updated_at` change côté bot : pas de
 * spam, le rythme est celui du bot (20s en monitoring, 90s au scan).
 * Une déconnexion relance automatiquement — le bot peut redémarrer sans que
 * le dashboard reste figé sans le dire.
 */
export function useLiveState() {
  const setState = useStore((s) => s.setState)
  const setTrades = useStore((s) => s.setTrades)
  const setEquitySeries = useStore((s) => s.setEquitySeries)
  const setShadow = useStore((s) => s.setShadow)
  const setConnected = useStore((s) => s.setConnected)
  const lastTradeCount = useRef(-1)

  useEffect(() => {
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let cancelled = false

    const loadRest = async () => {
      try {
        const [trades, shadow] = await Promise.all([
          fetch('/api/trades?arm=all').then((r) => r.json()),
          fetch('/api/shadow').then((r) => r.json()),
        ])
        setTrades(trades.trades ?? [])
        setEquitySeries(trades.equity_series ?? [])
        setShadow({
          total: shadow.total ?? 0,
          missed: shadow.missed ?? 0,
          missed_rate: shadow.missed_rate ?? 0,
        })
      } catch {
        /* l'API peut être down : le dashboard garde ses dernières données */
      }
    }

    const connect = () => {
      if (cancelled) return
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${location.host}/ws`)

      socket.onopen = () => setConnected(true)

      socket.onmessage = (event) => {
        let state
        try {
          state = JSON.parse(event.data)
        } catch (error) {
          console.warn('[useLiveState] frame WebSocket non-JSON ignorée', error)
          return
        }
        setState(state)
        // Recharger les trades seulement quand leur nombre change. Compté
        // sur la FLOTTE (`aggregate`), pas sur le seul témoin : sinon un
        // cycle sans clôture côté témoin ne rafraîchit ni la table ni la
        // courbe, même si sniper ou runner viennent de clôturer une position.
        const count = state?.aggregate?.closed_trades ?? state?.stats?.total_trades ?? 0
        if (count !== lastTradeCount.current) {
          lastTradeCount.current = count
          loadRest()
        }
      }

      socket.onclose = () => {
        setConnected(false)
        retryTimer = window.setTimeout(connect, RECONNECT_DELAY_MS)
      }
      socket.onerror = () => socket?.close()
    }

    loadRest()
    connect()

    return () => {
      cancelled = true
      window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [setState, setTrades, setEquitySeries, setShadow, setConnected])
}

/** Anime une valeur quand elle change : vert si elle monte, rouge si elle baisse. */
export function usePulse(value: number | null | undefined) {
  const previous = useRef(value)
  const direction = useRef<'up' | 'down' | null>(null)

  if (value != null && previous.current != null && value !== previous.current) {
    direction.current = value > previous.current ? 'up' : 'down'
  }
  previous.current = value

  return direction.current ? `pulse-${direction.current}` : ''
}
