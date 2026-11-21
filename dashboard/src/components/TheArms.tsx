import { useCallback, useState } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { EMPTY_ARRAY } from '../lib/empty'
import { Panel } from './Panel'
import { ExportCsvButton } from './ExportCsvButton'
import { ARM_COLUMNS } from '../lib/csvExports'
import { usd, pnlColor } from '../lib/format'
import { useExplore } from '../lib/useExplore'
import { ExploreBar, NoResults } from './explore/ExploreBar'
import { SortBar } from './explore/SortControls'
import { Drawer } from './explore/Drawer'
import { ArmDetail } from './explore/ArmDetail'
import { useDrawer, type OpenEvent } from './explore/useDrawer'
import type { Arm } from '../lib/types'

/**
 * Les 7 stratégies, chacune avec son entonnoir du cycle.
 *
 * Comble le trou principal du dashboard : `state.candidates` ne montrait que
 * le bras témoin, et les six autres n'existaient que dans `state.json`. Un
 * bras invisible est un bras qu'on ne débogue pas — c'est ainsi que trois
 * d'entre eux ont pu rejeter 100 % des candidats sans que ça se voie.
 */

type OpenHandler = ReturnType<ReturnType<typeof useDrawer<string>>['openFrom']>

const SORT_OPTIONS = [
  { key: 'nom', label: 'Nom' },
  { key: 'pnl', label: 'P&L' },
  { key: 'trades', label: 'Trades' },
  { key: 'positions', label: 'Positions' },
]
const DESC_FIRST = ['pnl', 'trades', 'positions']
const SEARCH_OF = (a: Arm) => [a.name, a.role, a.description]
const ACCESSORS = {
  nom: (a: Arm) => a.name,
  pnl: (a: Arm) => (a.stats ? a.stats.total_pnl_usd : null),
  trades: (a: Arm) => a.stats?.total_trades ?? a.trades,
  positions: (a: Arm) => a.stats?.open_positions ?? 0,
}
type Statut = 'tous' | 'position' | 'sans'
const inStatut = (arm: Arm, statut: Statut) =>
  statut === 'tous' || ((arm.stats?.open_positions ?? 0) > 0) === (statut === 'position')

/** Barre d'entonnoir : vu -> retenu -> qualifié. La chute EST l'information. */
function Funnel({ arm }: { arm: Arm }) {
  const seen = (arm.kept ?? 0) + (arm.rejected ?? 0)
  const kept = arm.kept ?? 0
  const qualified = arm.qualified ?? 0
  if (!seen) return <span className="text-[11px] text-dim">aucun candidat ce cycle</span>

  const keptPct = (kept / seen) * 100
  const qualPct = (qualified / seen) * 100

  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
        <motion.div
          className="absolute inset-y-0 left-0 bg-dim/50"
          initial={false}
          animate={{ width: `${keptPct}%` }}
          transition={{ duration: 0.4 }}
        />
        <motion.div
          className="absolute inset-y-0 left-0 bg-toxic"
          initial={false}
          animate={{ width: `${qualPct}%` }}
          transition={{ duration: 0.4 }}
        />
      </div>
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-dim">
        {seen}→{kept}→<span className={qualified ? 'text-toxic' : ''}>{qualified}</span>
      </span>
    </div>
  )
}

// Seuils calés sur les taux d'atteinte mesurés (26 positions instrumentées) :
// +50 % de pic franchi par 23 % des trades, ce qui met la barre haute d'un
// WR/PF sain nettement au-dessus de ce qu'un memecoin filtré au hasard donne.
const WR_GOOD = 33
const WR_OK = 20
const PF_GOOD = 1.5
const PF_OK = 1.0
const PF_SCALE_MAX = 2.0

function microBarColor(value: number, good: number, ok: number): string {
  if (value >= good) return 'var(--color-toxic)'
  if (value >= ok) return 'var(--color-warn)'
  return 'var(--color-blood)'
}

/** Deux micro-barres horizontales : WR et PF, lisibles sans déplier la ligne. */
function MicroBars({ winRate, profitFactor }: { winRate: number; profitFactor: number }) {
  const wrColor = microBarColor(winRate, WR_GOOD, WR_OK)
  const pfColor = microBarColor(profitFactor, PF_GOOD, PF_OK)
  const pfFill = Math.min(profitFactor, PF_SCALE_MAX) / PF_SCALE_MAX

  return (
    <span
      className="flex shrink-0 flex-col justify-center gap-[3px]"
      title={`WR ${winRate.toFixed(0)}% · PF ${profitFactor.toFixed(2)}`}
    >
      <span className="h-[3px] w-10 overflow-hidden rounded-full bg-white/10">
        <span
          className="block h-full rounded-full transition-[width]"
          style={{ width: `${Math.min(winRate, 100)}%`, backgroundColor: wrColor }}
        />
      </span>
      <span className="h-[3px] w-10 overflow-hidden rounded-full bg-white/10">
        <span
          className="block h-full rounded-full transition-[width]"
          style={{ width: `${pfFill * 100}%`, backgroundColor: pfColor }}
        />
      </span>
    </span>
  )
}

function ArmRow({ arm, onOpen }: { arm: Arm; onOpen: OpenHandler }) {
  const stats = arm.stats
  const pnl = stats?.total_pnl_usd ?? 0
  const trades = stats?.total_trades ?? arm.trades ?? 0
  const positions = stats?.open_positions ?? 0

  return (
    <div className="border-b border-white/5 last:border-0">
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        className="group min-h-11 w-full px-1 py-2 text-left transition-colors hover:bg-white/[0.03]"
      >
        <span className="sr-only">Détail de </span>
        <div className="flex items-baseline gap-2">
          <span className="w-[4.5rem] shrink-0 truncate font-mono text-xs text-ink">
            {arm.name}
          </span>
          {arm.role === 'consensus' && (
            <span className="shrink-0 rounded bg-white/10 px-1 text-[11px] text-dim">
              ≥{arm.min_confluence}
            </span>
          )}
          {positions > 0 && (
            <span className="shrink-0 rounded bg-toxic/20 px-1 text-[11px] text-toxic">
              {positions} en cours
            </span>
          )}
          <span className="flex-1" />
          {stats && trades > 0 && (
            <MicroBars winRate={stats.win_rate} profitFactor={stats.profit_factor} />
          )}
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-dim">
            {trades} trades
          </span>
          <span className={`w-16 shrink-0 text-right font-mono text-xs tabular-nums ${pnlColor(pnl)}`}>
            {trades ? usd(pnl) : '—'}
          </span>
        </div>
        <div className="mt-1.5 pl-[4.5rem]">
          <Funnel arm={arm} />
        </div>
      </button>
    </div>
  )
}

/** Tokens sur lesquels au moins deux stratégies s'accordent. */
function Confluence() {
  const rows = useStore((s) => s.state.confluence ?? EMPTY_ARRAY)
  const agreed = rows.filter((r) => r.above_threshold_count >= 2)

  if (!rows.length) {
    return <p className="px-1 py-2 text-[11px] text-dim">Aucun candidat retenu ce cycle.</p>
  }
  if (!agreed.length) {
    return (
      <p className="px-1 py-2 text-[11px] leading-snug text-dim">
        Aucun accord. Les fenêtres d'âge et de liquidité sont disjointes par
        construction&nbsp;: un token que <span className="text-ink">sniper</span> accepte
        n'entre dans la fenêtre d'aucun autre bras.
      </p>
    )
  }
  return (
    <ul className="space-y-1 px-1 py-1">
      {agreed.slice(0, 6).map((row) => (
        <li key={row.token_address} className="flex items-baseline gap-2 text-[11px]">
          <span className="w-20 shrink-0 truncate font-mono text-ink">{row.symbol}</span>
          <span className="shrink-0 rounded bg-toxic/20 px-1 font-mono text-[11px] text-toxic">
            {row.above_threshold_count}
          </span>
          <span className="truncate text-[11px] text-dim">
            {row.above_threshold_by.join(' · ')}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function TheArms() {
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const [statut, setStatut] = useState<Statut>('tous')
  const predicate = useCallback((a: Arm) => inStatut(a, statut), [statut])
  const explore = useExplore({
    rows: arms,
    searchOf: SEARCH_OF,
    accessors: ACCESSORS,
    predicate,
    descFirst: DESC_FIRST,
    filterActive: statut !== 'tous',
    onReset: () => setStatut('tous'),
  })
  // `panel` (store, relié à l'URL) désigne le bras ouvert : la palette Cmd+K
  // l'écrit (« Bras X »), et le tiroir le met à jour à l'ouverture/fermeture.
  const panel = useStore((s) => s.panel)
  const setPanel = useStore((s) => s.setPanel)
  const drawer = useDrawer<string>()
  const selected = arms.find((a) => a.name === panel) ?? null
  const openArm = (name: string) => (event: OpenEvent) => {
    drawer.openFrom(name)(event)
    setPanel(name)
  }
  const closeArm = () => {
    drawer.close()
    if (selected) setPanel(null)
  }

  if (!arms.length) {
    return (
      <Panel title="Stratégies">
        <p className="px-1 py-3 text-[11px] leading-snug text-dim">
          Aucune stratégie publiée. Le bot tourne-t-il&nbsp;?
        </p>
      </Panel>
    )
  }

  const totalPnl = arms.reduce((sum, a) => sum + (a.stats?.total_pnl_usd ?? 0), 0)
  const totalTrades = arms.reduce((sum, a) => sum + (a.stats?.total_trades ?? 0), 0)
  const active = arms.filter((a) => (a.stats?.open_positions ?? 0) > 0).length

  return (
    <Panel title={`Stratégies · ${arms.length}`}>
      <div className="mb-2 flex items-baseline gap-3 px-1 font-mono text-[11px] text-dim">
        <span>
          {totalTrades} trades cumulés
        </span>
        <span className={pnlColor(totalPnl)}>{usd(totalPnl)}</span>
        <span className="flex-1" />
        <span>{active} en position</span>
      </div>

      <ExploreBar
        label="Chercher une stratégie"
        placeholder="Nom ou rôle"
        query={explore.query}
        onQuery={explore.setQuery}
        shown={explore.visible.length}
        total={explore.total}
        isFiltered={explore.isFiltered}
        onReset={explore.reset}
      >
        <select
          aria-label="Statut"
          value={statut}
          onChange={(event) => setStatut(event.target.value as Statut)}
          className="min-h-11 rounded-md bg-void px-2 font-mono text-xs text-ink ring-1 ring-edge "
        >
          <option value="tous">tous les statuts</option>
          <option value="position">en position</option>
          <option value="sans">sans position</option>
        </select>
      </ExploreBar>
      <div className="mb-2 px-1">
        <SortBar options={SORT_OPTIONS} sort={explore.sort} onSort={explore.toggleSort} />
      </div>

      {/* Le détail des règles s'ouvre dans un tiroir : la liste reste lisible
          à 7 stratégies et garde sa place pendant la consultation. */}
      {explore.visible.length === 0 ? (
        <NoResults query={explore.query} />
      ) : (
        <div>
          {explore.visible.map((arm) => (
            <ArmRow key={arm.name} arm={arm} onOpen={openArm(arm.name)} />
          ))}
        </div>
      )}

      <p className="mt-2 px-1 text-[11px] leading-snug text-dim">
        barre&nbsp;: vus → retenus → au-dessus du seuil. La chute est
        l'information&nbsp;— un bras qui ne qualifie rien a un filtre à revoir.
      </p>

      <div className="mt-2">
        <ExportCsvButton rows={arms} columns={ARM_COLUMNS} kind="bras" label="Exporter les bras" />
      </div>

      <h3 className="mb-1 mt-4 text-[11px] uppercase tracking-wide text-dim">Confluence</h3>
      <Confluence />

      <Drawer
        open={selected !== null}
        onClose={closeArm}
        title={selected?.name ?? ''}
        subtitle={selected ? (selected.enabled ? 'stratégie' : 'stratégie désactivée') : undefined}
      >
        {selected && <ArmDetail arm={selected} />}
      </Drawer>
    </Panel>
  )
}
