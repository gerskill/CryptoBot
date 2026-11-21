import { Panel, Empty } from '../Panel'
import { usePilotageStore } from '../../lib/pilotageStore'
import { entryStatus } from '../../lib/pilotage'
import { utc } from '../../lib/format'

const TONE = { refusee: 'text-warn', transmise: 'text-ink', perimee: 'text-warn', deposee: 'text-ink' } as const

/** Les dernières commandes envoyées DEPUIS CE NAVIGATEUR, et leur statut. */
export function PilotageJournal() {
  const journal = usePilotageStore((s) => s.journal)
  const now = Date.now()
  return (
    <Panel title="Journal des commandes" count={journal.length}
      hint="Gardé dans ce navigateur seulement : libellé et résultat, jamais le jeton.">
      {journal.length === 0 ? <Empty>Aucune commande envoyée depuis ce navigateur.</Empty> : (
        <ul className="grid gap-1.5 px-1 text-[11px]">
          {journal.map((e) => {
            const s = entryStatus(e, now)
            return (
              <li key={e.id} className="rounded-md px-2 py-1.5 ring-1 ring-edge/40">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="font-mono text-ink">{e.label}</span>
                  <span className="font-mono text-dim">{utc(new Date(e.t).toISOString())}</span>
                </div>
                <div className={`mt-0.5 leading-snug ${TONE[s.tone]}`}>
                  {s.text}{!e.ok || e.kind === 'liquid' ? ` : ${e.detail}` : ''}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Panel>
  )
}
