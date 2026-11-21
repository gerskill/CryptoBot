import { useAlertStore } from '../../lib/alertStore'
import type { AlertEntry } from '../../lib/alerts'
import { WarnIcon } from './icons'

const timeFormat = new Intl.DateTimeFormat('fr-CH', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

function Row({ alert }: { alert: AlertEntry }) {
  const markRead = useAlertStore((s) => s.markRead)
  return (
    <li>
      <button
        type="button"
        onClick={() => markRead(alert.id)}
        className="flex min-h-11 w-full items-start gap-2 rounded-md px-2 py-2 text-left hover:bg-raised"
      >
        <WarnIcon className="mt-0.5 shrink-0 text-warn" />
        <span className="min-w-0 flex-1">
          <span className={`block text-[12px] ${alert.read ? 'text-dim' : 'font-semibold text-ink'}`}>
            {alert.title}
          </span>
          <span className="block text-[11px] leading-snug text-dim">{alert.detail}</span>
        </span>
        <span className="shrink-0 text-right font-mono text-[11px] text-dim">
          {timeFormat.format(alert.at)}
          <span className={`block ${alert.read ? '' : 'text-warn'}`}>{alert.read ? 'lu' : 'non lu'}</span>
        </span>
      </button>
    </li>
  )
}

export function AlertList() {
  const alerts = useAlertStore((s) => s.alerts)
  const markAllRead = useAlertStore((s) => s.markAllRead)
  const unread = alerts.filter((a) => !a.read).length

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.15em] text-dim">
          Dernières alertes · {unread} non lue{unread > 1 ? 's' : ''}
        </h3>
        <button
          type="button"
          onClick={markAllRead}
          disabled={unread === 0}
          className="min-h-11 px-2 text-[12px] text-muted underline-offset-2 hover:underline disabled:opacity-40"
        >
          Tout marquer lu
        </button>
      </div>
      {alerts.length === 0 ? (
        <p className="py-3 text-[12px] text-dim">Aucune alerte pour l'instant.</p>
      ) : (
        <ul className="max-h-64 overflow-y-auto">
          {alerts.map((alert) => (
            <Row key={alert.id} alert={alert} />
          ))}
        </ul>
      )}
    </div>
  )
}
