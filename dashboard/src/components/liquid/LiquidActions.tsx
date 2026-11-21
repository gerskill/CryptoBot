import { useState } from 'react'
import { Panel, Empty } from '../Panel'
import { usePolled } from '../../lib/usePrediction'
import type { PredictionArm, PredictionRequest } from '../../lib/predictionTypes'
import { utc } from '../../lib/format'
import { REASON_LABEL, sendRequest } from './liquidRequests'

type Requests = { total: number; requests: PredictionRequest[]; bornes: Record<string, [number, number]>; max_forks: number }

const VARIABLE_LABEL: Record<string, string> = {
  fourchette: 'Fourchette de prix acceptée',
  cote: 'Côté acheté',
  edge_min: 'Espérance minimale pour entrer',
  estimateur: 'Estimateur',
  max_jours: 'Échéance maximale (jours)',
  min_minutes: 'Délai minimal avant l’échéance (minutes)',
}

const field = 'w-full rounded-md bg-void px-2.5 py-2 font-mono text-[11px] text-ink outline-none ring-1 ring-edge/60 focus:ring-tide/60'

function ForkForm({ arms, token, onSent }: { arms: PredictionArm[]; token: string; onSent: () => void }) {
  const parents = arms.filter((a) => a.variables_forkables.length > 0)
  const [parentName, setParentName] = useState(parents[0]?.arm ?? '')
  const parent = parents.find((a) => a.arm === parentName) ?? parents[0]
  const [variableChoice, setVariable] = useState('')
  const variable = parent?.variables_forkables.includes(variableChoice) ? variableChoice : parent?.variables_forkables[0] ?? ''
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const [low, setLow] = useState('')
  const [high, setHigh] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  if (!parent) return <Empty>Aucun bras ne peut être forké.</Empty>
  const current = parent.reglages

  const value = (): unknown => {
    if (variable === 'fourchette') return { prix_min: Number(low) / 100, prix_max: Number(high) / 100 }
    if (variable === 'cote' || variable === 'estimateur') return text
    if (variable === 'edge_min') return Number(text) / 100
    return Number(text)
  }
  const currentText = variable === 'fourchette'
    ? `${Math.round(Number(current.prix_min ?? 0.5) * 100)} % à ${Math.round(Number(current.prix_max ?? 0.95) * 100)} %`
    : variable === 'edge_min' ? `${(Number(current.edge_min ?? 0.05) * 100).toFixed(0)} points`
    : String(current[variable] ?? (variable === 'cote' ? 'favori' : '—'))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    const error = await sendRequest({ action: 'fork', parent: parent.arm, name, variable, value: value() }, token)
    setBusy(false)
    setMessage(error ? { ok: false, text: `Refusé : ${error}` }
      : { ok: true, text: `Demande déposée. ${name} apparaîtra au prochain cycle de la simulation.` })
    if (!error) { setName(''); onSent() }
  }

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-3 px-1 sm:grid-cols-2 xl:grid-cols-4">
      <label className="text-[11px] uppercase tracking-wider text-dim">
        Bras parent
        <select value={parent.arm} onChange={(e) => { setParentName(e.target.value); setMessage(null) }} className={`mt-1 ${field}`}>
          {parents.map((a) => <option key={a.arm} value={a.arm}>{a.arm}</option>)}
        </select>
      </label>
      <label className="text-[11px] uppercase tracking-wider text-dim">
        La seule variable changée
        <select value={variable} onChange={(e) => { setVariable(e.target.value); setText('') }} className={`mt-1 ${field}`}>
          {parent.variables_forkables.map((v) => <option key={v} value={v}>{VARIABLE_LABEL[v] ?? v}</option>)}
        </select>
      </label>
      <div className="text-[11px] uppercase tracking-wider text-dim">
        Nouvelle valeur <span className="normal-case tracking-normal text-muted">(parent : {currentText})</span>
        {variable === 'fourchette' ? (
          <div className="mt-1 flex items-center gap-2">
            <input required type="number" min={2} max={98} value={low} onChange={(e) => setLow(e.target.value)} placeholder="min %" className={field} />
            <input required type="number" min={2} max={98} value={high} onChange={(e) => setHigh(e.target.value)} placeholder="max %" className={field} />
          </div>
        ) : variable === 'cote' || variable === 'estimateur' ? (
          <select required value={text} onChange={(e) => setText(e.target.value)} className={`mt-1 ${field}`}>
            <option value="">choisir…</option>
            {(variable === 'cote' ? ['favori', 'outsider'] : ['empirique', 'lognormal']).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        ) : (
          <input required type="number" step="any" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={variable === 'edge_min' ? 'points, de 1 à 30' : variable === 'max_jours' ? 'jours, jusqu’à 60' : 'minutes, de 1 à 1440'}
            className={`mt-1 ${field}`} />
        )}
      </div>
      <label className="text-[11px] uppercase tracking-wider text-dim">
        Nom du nouveau bras
        <input required value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="pm_sport_favori_70"
          pattern="pm_[a-z0-9_]{3,30}" className={`mt-1 ${field}`} />
      </label>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-2 xl:col-span-4">
        <button type="submit" disabled={busy}
          className="rounded-md bg-tide/15 px-4 py-2 font-mono text-[11px] uppercase tracking-wider text-tide ring-1 ring-tide/40 transition-colors hover:bg-tide/25 disabled:opacity-50">
          Créer le fork
        </button>
        {message && <span className={`text-[11px] ${message.ok ? 'text-toxic' : 'text-blood'}`}>{message.text}</span>}
      </div>
    </form>
  )
}

/** Zone des modifications : forks, jeton, et suivi de chaque demande. */
export function LiquidActions({ arms, token, setToken, reloadKey, onSent }: {
  arms: PredictionArm[]; token: string; setToken: (t: string) => void; reloadKey: number; onSent: () => void
}) {
  const { data } = usePolled<Requests>(`/api/prediction/requests?k=${reloadKey}`, 10_000)
  const rows = (data?.requests ?? []).slice().reverse().slice(0, 12)
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <Panel title="Créer un bras de test (fork)"
        hint="Un bras existant ne se modifie pas : ses paris déjà pris ne seraient plus comparables. On crée un fork qui ne change qu’une variable, dans des bornes fixes. Capital fictif, aucun pari réel.">
        <ForkForm arms={arms} token={token} onSent={onSent} />
        <label className="mt-4 block px-1 text-[11px] uppercase tracking-wider text-dim">
          Jeton du site <span className="normal-case tracking-normal text-muted">(seulement si DASHBOARD_TOKEN est défini ; gardé en mémoire, jamais enregistré)</span>
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
            className={`mt-1 max-w-xs ${field}`} />
        </label>
      </Panel>
      <Panel title="Demandes déposées" count={data?.total ?? 0}
        hint="La simulation valide et applique chaque demande au début de son prochain cycle.">
        {rows.length === 0 ? <Empty>Aucune demande pour l’instant.</Empty> : (
          <ul className="grid gap-1.5 text-[11px]">
            {rows.map((r) => (
              <li key={r.id} className="flex flex-wrap items-baseline justify-between gap-x-3 rounded-md px-2 py-1.5 ring-1 ring-edge/40">
                <span className="font-mono text-ink">
                  {r.action === 'fork' ? `fork ${r.name} ← ${r.parent} (${r.variable})` : `${r.enabled ? 'activer' : 'désactiver'} ${r.arm}`}
                </span>
                <span className={r.statut === 'appliquée' ? 'text-toxic' : r.statut === 'refusée' ? 'text-blood' : 'text-warn'}>
                  {r.statut}{r.raison ? ` : ${REASON_LABEL[r.raison] ?? r.raison}` : ''}
                </span>
                <span className="w-full font-mono text-[11px] text-dim">{utc(r.t)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}
