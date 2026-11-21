import { useEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { useAlertStore, selectUnread } from './alertStore'
import { EMPTY_ARRAY } from './empty'
import {
  dedupeAlerts,
  detectAlerts,
  isBotStopped,
  isLiquidStopped,
  summarizeBatch,
  type DedupeMemory,
  type LiquidHealth,
} from './alerts'
import { playBeep, showSystemNotification, tabTitle } from './notify'

const TICK_MS = 15_000
const LIQUID_POLL_MS = 30_000
const DEDUPE = { windowMs: 30 * 60_000, minGapMs: 60_000 }

/** Santé de la boucle Liquid : sondée seulement quand les alertes sont activées. */
function useLiquidHealth(active: boolean): LiquidHealth | null {
  const [health, setHealth] = useState<LiquidHealth | null>(null)

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    const poll = () => {
      fetch('/api/prediction/summary', { signal: controller.signal })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((body: { runner_online?: boolean; updated_at_iso?: string | null; exemple_fictif?: boolean }) =>
          setHealth({
            demo: body.exemple_fictif === true,
            runnerOnline: body.runner_online === true,
            updatedAtIso: body.updated_at_iso ?? null,
          }),
        )
        .catch(() => {
          /* API injoignable : on garde le dernier état connu, pas de fausse alerte */
        })
    }
    poll()
    const id = setInterval(poll, LIQUID_POLL_MS)
    return () => {
      controller.abort()
      clearInterval(id)
    }
  }, [active])

  return active ? health : null
}

/**
 * Surveille l'état déjà poussé par le WebSocket et alimente le centre
 * d'alertes. Ne demande JAMAIS la permission de notifier : c'est le bouton
 * explicite des réglages. Met aussi le compteur dans le titre d'onglet.
 */
export function useAlertMonitor(): void {
  const settings = useAlertStore((s) => s.settings)
  const addAlerts = useAlertStore((s) => s.addAlerts)
  const unread = useAlertStore(selectUnread)
  const state = useStore((s) => s.state)
  const connected = useStore((s) => s.connected)
  const arms = state.arms ?? EMPTY_ARRAY
  const positions = state.positions ?? EMPTY_ARRAY
  const liquid = useLiquidHealth(settings.enabled)
  const [now, setNow] = useState(() => Date.now())
  const memory = useRef<DedupeMemory>({ lastSeen: {}, lastNotifiedAt: 0 })
  const baseTitle = useRef(document.title)

  // L'état d'un bot mort ne change plus : une horloge relance l'évaluation.
  useEffect(() => {
    if (!settings.enabled) return
    const id = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(id)
  }, [settings.enabled])

  useEffect(() => {
    if (!settings.enabled) {
      memory.current = { lastSeen: {}, lastNotifiedAt: 0 }
      return
    }
    const candidates = detectAlerts(
      {
        arms,
        positions,
        botStopped: isBotStopped(state, connected, now),
        liquidStopped: isLiquidStopped(liquid, now),
      },
      settings,
    )
    const result = dedupeAlerts(candidates, memory.current, now, DEDUPE)
    memory.current = result.memory
    if (result.fresh.length === 0) return
    addAlerts(result.fresh, now)
    if (result.toNotify.length > 0) showSystemNotification(summarizeBatch(result.toNotify))
    if (settings.sound) playBeep()
  }, [settings, arms, positions, state, connected, liquid, now, addAlerts])

  useEffect(() => {
    document.title = tabTitle(baseTitle.current, unread)
  }, [unread])
}
