import { selectUnread, useAlertStore } from '../../lib/alertStore'
import { WarnIcon } from './icons'

const MAX_SHOWN = 3

/**
 * Bandeau dans la page : repli quand les notifications système ne sont pas
 * autorisées (refusées, non prises en charge ou pas encore demandées).
 */
export function AlertBanner() {
  const permission = useAlertStore((s) => s.permission)
  const enabled = useAlertStore((s) => s.settings.enabled)
  const alerts = useAlertStore((s) => s.alerts)
  const unread = useAlertStore(selectUnread)
  const markAllRead = useAlertStore((s) => s.markAllRead)

  if (!enabled || permission === 'granted' || unread === 0) return null
  const shown = alerts.filter((a) => !a.read).slice(0, MAX_SHOWN)

  return (
    <div role="alert" className="mb-4 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <ul className="space-y-1.5">
          {shown.map((alert) => (
            <li key={alert.id} className="flex items-start gap-2 text-[12px] text-warn">
              <WarnIcon className="mt-0.5 shrink-0" />
              <span>
                <strong className="font-semibold">{alert.title}</strong>
                <span className="text-warn/80"> — {alert.detail}</span>
              </span>
            </li>
          ))}
          {unread > shown.length && (
            <li className="text-[11px] text-warn/80">+ {unread - shown.length} autre(s) dans le centre d'alertes</li>
          )}
        </ul>
        <button
          type="button"
          onClick={markAllRead}
          className="min-h-11 shrink-0 px-2 text-[12px] text-warn underline-offset-2 hover:underline"
        >
          Tout marquer lu
        </button>
      </div>
    </div>
  )
}
