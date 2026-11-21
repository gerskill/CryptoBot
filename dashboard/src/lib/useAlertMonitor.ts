import { useEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { useAlertStore, selectUnread } from './alertStore'
import { EMPTY_ARRAY } from './empty'
import {
  LIQUID_POLL_MS,
  dedupeAlerts,
  detectAlerts,
  isBotStopped,
  isLiquidStopped,
  primeAlerts,
  summarizeBatch,
  type AlertCandidate,
  type DedupeMemory,
  type LiquidHealth,
} from './alerts'
import { clearMemory, loadMemory, saveMemory } from './dedupeStorage'
import { playBeep, showSystemNotification, tabTitle } from './notify'

const TICK_MS = 15_000
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
            fetchedAt: Date.now(),
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
const isLiquid = (candidate: AlertCandidate) => candidate.kind === 'liquid-stopped'

/**
 * Surveille l'état déjà poussé par le WebSocket et alimente le centre
 * d'alertes. Ne demande JAMAIS la permission de notifier : c'est le bouton
 * explicite des réglages. Met aussi le compteur dans le titre d'onglet.
 *
 * Premier passage (ouverture, rechargement, activation) : l'état existant est
 * AMORCÉ — listé comme lu, sans notification ni bip. Seules les transitions
 * suivantes notifient. L'amorçage attend la première trame réelle (et la
 * première réponse Liquid pour l'alerte Liquid).
 */
export function useAlertMonitor(): void {
  const settings = useAlertStore((s) => s.settings)
  const addAlerts = useAlertStore((s) => s.addAlerts)
  const unread = useAlertStore(selectUnread)
  const state = useStore((s) => s.state)
  const connected = useStore((s) => s.connected)
  const lastFrameAt = useStore((s) => s.lastFrameAt)
  const connectedAt = useStore((s) => s.connectedAt)
  const arms = state.arms ?? EMPTY_ARRAY
  const positions = state.positions ?? EMPTY_ARRAY
  const liquid = useLiquidHealth(settings.enabled)
  const [now, setNow] = useState(() => Date.now())
  const memory = useRef<DedupeMemory>(loadMemory())
  const primed = useRef({ base: false, liquid: false })
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
      primed.current = { base: false, liquid: false }
      clearMemory()
      return
    }
    if (lastFrameAt === null) return // aucune trame reçue : état inconnu
    const at = Date.now()
    const candidates = detectAlerts(
      {
        arms,
        positions,
        botStopped: isBotStopped(state, connected, { lastFrameAt, connectedAt }, at),
        liquidStopped: isLiquidStopped(liquid, at),
      },
      settings,
    )
    const liquidReady = liquid !== null
    const toPrime = candidates.filter((c) => (isLiquid(c) ? liquidReady && !primed.current.liquid : !primed.current.base))
    const live = candidates.filter((c) => !toPrime.includes(c))
    primed.current = { base: true, liquid: primed.current.liquid || liquidReady }

    const seeded = primeAlerts(toPrime, memory.current, at)
    if (seeded.fresh.length > 0) addAlerts(seeded.fresh, at, true)
    const result = dedupeAlerts(live, seeded.memory, at, DEDUPE)
    memory.current = result.memory
    saveMemory(result.memory)
    if (result.fresh.length === 0) return
    addAlerts(result.fresh, at)
    if (result.toNotify.length > 0) showSystemNotification(summarizeBatch(result.toNotify))
    if (settings.sound) playBeep()
  }, [settings, arms, positions, state, connected, lastFrameAt, connectedAt, liquid, now, addAlerts])

  useEffect(() => {
    document.title = tabTitle(baseTitle.current, unread)
  }, [unread])
}
