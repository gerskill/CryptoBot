import { useState } from 'react'
import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, NoSource, Empty } from '../Panel'
import { relativeTime } from '../../lib/format'
import { useArmParams } from '../../lib/useArmParams'
import { ArmPicker } from './ArmPicker'
import { TokenGate } from './TokenGate'
import { ParamSection } from './ParamSection'

const HISTORY_VISIBLE_COUNT = 5

type HistoryEntry = {
  param_name?: string
  old_value?: unknown
  new_value?: unknown
  timestamp?: string
}

function formatEntryValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value !== 'object') return String(value)
  return '…'
}

/** Les 5 derniers ajustements d'apprentissage, même grammaire que LearningFeed. */
function HistoryList({ history }: { history: unknown[] }) {
  const entries = history as HistoryEntry[]
  if (entries.length === 0) return null
  const visible = entries.slice(0, HISTORY_VISIBLE_COUNT)

  return (
    <div className="mt-4">
      <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-dim">Derniers ajustements</h3>
      <ul className="space-y-1">
        {visible.map((entry, index) => (
          <li
            key={index}
            className="flex items-center gap-2 rounded-md border border-gem/25 bg-gem/[0.04] px-2 py-1 font-mono text-[10px]"
          >
            <span className="text-gem">↗</span>
            <span className="tabular-nums text-ink">
              {formatEntryValue(entry.old_value)} → {formatEntryValue(entry.new_value)}
            </span>
            <span className="truncate text-dim">{entry.param_name ?? ''}</span>
            <span className="ml-auto shrink-0 text-dim">
              {entry.timestamp ? relativeTime(entry.timestamp) : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Paramètres d'un bras, lecture seule. Gère le jeton d'auth avec grâce. */
export function ConfigEditor() {
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const [selected, setSelected] = useState<string | null>(null)
  const [token, setToken] = useState('')

  const activeArm = selected ?? arms[0]?.name ?? null
  const result = useArmParams(activeArm, token)

  if (arms.length === 0) {
    return (
      <Panel title="Configuration">
        <Empty>Aucune stratégie publiée. Le bot tourne-t-il ?</Empty>
      </Panel>
    )
  }

  return (
    <Panel title="Configuration">
      <ArmPicker arms={arms} selected={activeArm} onSelect={setSelected} />

      <div className="mt-3">
        {result.status === 'idle' && (
          <p className="py-6 text-center text-[11px] text-dim">Sélectionne une stratégie.</p>
        )}
        {result.status === 'loading' && (
          <p className="py-6 text-center text-[11px] text-dim">Chargement des paramètres…</p>
        )}
        {result.status === 'unauthorized' && <TokenGate onSubmit={setToken} />}
        {result.status === 'error' && <NoSource label="Paramètres indisponibles" why={result.message} />}
        {result.status === 'ok' && (
          <>
            <ParamSection params={result.params} />
            <HistoryList history={result.history} />
          </>
        )}
      </div>
    </Panel>
  )
}
