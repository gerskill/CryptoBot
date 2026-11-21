import { useState } from 'react'
import { useAlertStore } from '../../lib/alertStore'
import { requestNotificationPermission, type NotificationState } from '../../lib/notify'

const PERMISSION_LABEL: Record<NotificationState, string> = {
  granted: 'Notifications système : autorisées',
  denied: 'Notifications système : bloquées par le navigateur (bandeau dans la page à la place)',
  default: 'Notifications système : pas encore autorisées (bandeau dans la page en attendant)',
  unsupported: 'Notifications système : non prises en charge (bandeau dans la page à la place)',
}

const BUTTON =
  'min-h-11 rounded-md px-3 text-[12px] font-semibold ring-1 ring-edge-strong transition-colors hover:bg-raised active:scale-[0.97]'

/** Réglages des alertes. Le bouton est le SEUL endroit qui demande la permission. */
export function AlertSettingsForm() {
  const settings = useAlertStore((s) => s.settings)
  const permission = useAlertStore((s) => s.permission)
  const setSettings = useAlertStore((s) => s.setSettings)
  const setPermission = useAlertStore((s) => s.setPermission)
  const [draft, setDraft] = useState(String(settings.lossPct))

  const enable = async () => {
    setSettings({ enabled: true })
    setPermission(await requestNotificationPermission())
  }
  const commitLoss = () => {
    const value = Number(draft.replace(',', '.'))
    if (Number.isFinite(value) && value > 0) setSettings({ lossPct: value })
    setDraft(String(useAlertStore.getState().settings.lossPct))
  }

  return (
    <div className="space-y-3 text-[12px]">
      <p className="leading-snug text-muted" role="status">
        {settings.enabled ? 'Alertes activées. ' : 'Alertes désactivées. '}
        {PERMISSION_LABEL[permission]}
      </p>
      <div className="flex flex-wrap gap-2">
        {settings.enabled ? (
          <button type="button" className={`${BUTTON} text-ink`} onClick={() => setSettings({ enabled: false })}>
            Désactiver les alertes
          </button>
        ) : (
          <button type="button" className={`${BUTTON} text-ink`} onClick={enable}>
            Activer les notifications
          </button>
        )}
      </div>
      <label className="block">
        <span className="text-muted">Perte d'un bras à signaler (% de sa mise de départ)</span>
        <input
          type="number"
          inputMode="decimal"
          min={0.5}
          max={100}
          step={0.5}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitLoss}
          onKeyDown={(event) => event.key === 'Enter' && commitLoss()}
          className="mt-1 block min-h-11 w-28 rounded-md bg-void px-3 font-mono text-ink ring-1 ring-edge-strong"
        />
      </label>
      <button
        type="button"
        role="switch"
        aria-checked={settings.sound}
        onClick={() => setSettings({ sound: !settings.sound })}
        className="flex min-h-11 items-center gap-2 text-muted"
      >
        <span className={`${BUTTON} flex min-h-8 items-center px-2 font-mono text-ink`}>
          {settings.sound ? 'OUI' : 'NON'}
        </span>
        Bip sonore à chaque nouvelle alerte
      </button>
    </div>
  )
}
