import { useCallback, useEffect, useState } from 'react'
import { Panel } from '../Panel'
import { usePilotageStore } from '../../lib/pilotageStore'
import { sendBotCommand, type BotAction, type Outcome } from '../../lib/pilotage'
import { ConfirmDialog } from './ConfirmDialog'

const POLL_MS = 10_000
const field = 'w-full rounded-md bg-void px-2.5 py-2 font-mono text-xs text-ink outline-none ring-1 ring-edge/60 focus:ring-gem/60'
const button = 'min-h-11 rounded-md px-4 font-mono text-[11px] uppercase tracking-wider disabled:opacity-50'

const LABEL: Record<BotAction, string> = { stop: 'Arrêt d’urgence', resume: 'Reprendre les entrées' }

/** Commandes lues par la boucle mais pas encore traitées ; `null` = inconnu (API muette ou jeton absent). */
function usePending(token: string, reloadKey: number): number | null {
  const [pending, setPending] = useState<number | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    const load = () => {
      const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
      fetch('/api/bot/commandes', { headers, signal: controller.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { en_attente?: unknown } | null) => setPending(typeof d?.en_attente === 'number' ? d.en_attente : null))
        .catch(() => setPending(null))
    }
    load()
    const id = setInterval(load, POLL_MS)
    return () => { controller.abort(); clearInterval(id) }
  }, [token, reloadKey])
  return pending
}

/**
 * Arrêt d'urgence et reprise du bot memecoins (PAPIER : positions fictives).
 * Même chemin que l'iPhone et Telegram : on dépose une commande, la boucle la
 * lit à son prochain cycle. Le serveur valide ; rien n'est exécuté ici.
 */
export function BotControls() {
  const record = usePilotageStore((s) => s.record)
  const [token, setToken] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Outcome | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const pending = usePending(token, reloadKey)

  const send = useCallback(async (action: BotAction) => {
    setBusy(true)
    setResult(null)
    const outcome = await sendBotCommand(action, token)
    setBusy(false)
    setConfirming(false)
    setResult(outcome)
    record({ label: LABEL[action], kind: 'bot', ok: outcome.ok, detail: outcome.detail })
    setReloadKey((k) => k + 1)
  }, [token, record])

  return (
    <Panel title="Piloter le bot memecoins"
      hint="Mode papier : aucune transaction réelle. La commande est déposée dans la boîte de la boucle, qui l’applique à son prochain cycle (comme /STOP et /RESUME sur Telegram).">
      <div className="grid gap-4 px-1">
        <div className="flex flex-col gap-3 sm:flex-row">
          <button type="button" disabled={busy} onClick={() => { setResult(null); setConfirming(true) }}
            className={`${button} bg-warn/15 text-warn ring-1 ring-warn/50 hover:bg-warn/25`}>
            {LABEL.stop}
          </button>
          <button type="button" disabled={busy} onClick={() => void send('resume')}
            className={`${button} bg-raised text-ink ring-1 ring-edge-strong hover:ring-gem/60`}>
            {busy && !confirming ? 'Envoi en cours…' : LABEL.resume}
          </button>
        </div>

        {result && (result.ok
          ? <p role="status" className="text-[13px] leading-snug text-ink">Appliquée : {result.detail}</p>
          : <p role="alert" className="text-[13px] leading-snug text-warn">Refusée. {result.detail}</p>)}

        <p className="text-[11px] leading-snug text-dim">
          Une commande que la boucle n’a pas lue au bout de 15 min est refusée (boucle arrêtée) : elle ne partira pas
          au redémarrage.{pending !== null && ` En attente de lecture : ${pending}.`}
        </p>

        <label className="block text-[11px] uppercase tracking-wider text-dim">
          Jeton du site <span className="normal-case tracking-normal text-muted">(seulement si DASHBOARD_TOKEN est défini ; gardé en mémoire, jamais enregistré)</span>
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
            className={`mt-1 max-w-xs ${field}`} />
        </label>
      </div>

      {confirming && (
        <ConfirmDialog title="Arrêt d’urgence du bot"
          consequence="Ferme toutes les positions papier ouvertes et met le bot en pause : plus aucune entrée tant que vous ne reprenez pas. Les positions fermées ne se rouvrent pas."
          confirmLabel="Fermer toutes les positions" busy={busy}
          onConfirm={() => void send('stop')} onCancel={() => setConfirming(false)} />
      )}
    </Panel>
  )
}
