import { useState } from 'react'
import { Panel, Stat, Empty } from '../Panel'
import { LiquidActions } from './LiquidActions'
import { sendRequest } from './liquidRequests'
import type { PredictionArm, PredictionSummary } from '../../lib/predictionTypes'
import { pnlColor } from '../../lib/format'
import { DOMAIN_LABEL, FAMILLES, ROLE_LABEL, signed } from './liquidLabels'

/** Une carte par bras : qui il est, de qui il descend, où il en est. */
function ArmCard({ arm, need, onToggle }: {
  arm: PredictionArm; need: number; onToggle: ((arm: PredictionArm) => void) | null
}) {
  const done = arm.stats.paris_regles
  const progress = Math.min(1, done / need)
  const observer = arm.role === 'observateur'
  return (
    <div className={`rounded-md bg-raised/60 px-3 py-2.5 ring-1 ring-edge/60 ${arm.enabled ? '' : 'opacity-50'}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[12px] text-ink">{arm.arm}</span>
        <span className="shrink-0 rounded bg-tide/10 px-1.5 py-px text-[9px] uppercase tracking-wider text-tide">
          {arm.enabled ? (ROLE_LABEL[arm.role] ?? arm.role) : 'désactivé'}
        </span>
      </div>
      {arm.cree_depuis_le_site && (
        <div className="mt-1 text-[10px] text-warn">
          {arm.origine === 'apprentissage' ? 'créé par l’agent d’apprentissage' : 'créé depuis le site'}
        </div>
      )}
      {arm.parent && (
        <div className="mt-1 font-mono text-[10px] text-dim">
          ↳ fork de {arm.parent} · variable : {arm.variable ?? '—'}
        </div>
      )}
      <p className="mt-1.5 text-[11px] leading-snug text-muted">{arm.description}</p>
      <div className="mt-2 text-[10px] text-dim">
        {arm.domaines.map((d) => DOMAIN_LABEL[d] ?? d).join(' · ')}
      </div>
      {observer ? (
        <div className="mt-2 text-[10px] text-dim">Ne parie pas : voir l’onglet Incohérences.</div>
      ) : (
        <>
          <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-edge">
            <div className="h-full rounded-full bg-tide" style={{ width: `${progress * 100}%` }} />
          </div>
          <div className="mt-1.5 flex items-baseline justify-between font-mono text-[10px] tabular-nums">
            <span className="text-muted">{done} / {need} réglés · {arm.ouverts} ouverts</span>
            <span className={done ? pnlColor(arm.stats.pnl_net) : 'text-dim'}>
              {done ? signed(arm.stats.pnl_net) : '—'}
            </span>
          </div>
          {arm.compte && (
            <div className="mt-1 flex items-baseline justify-between font-mono text-[10px] tabular-nums text-dim">
              <span>compte {arm.compte.valeur.toFixed(2)} $</span>
              <span>solde {arm.compte.solde.toFixed(2)} $ · engagé {arm.compte.engage.toFixed(2)} $</span>
            </div>
          )}
          {arm.apprentissage?.decision && (
            <div className="mt-1.5 text-[10px] leading-snug text-muted">
              <span className="text-tide">Agent :</span> {arm.apprentissage.decision}
            </div>
          )}
        </>
      )}
      {onToggle && (
        <button onClick={() => onToggle(arm)}
          className="mt-2.5 rounded px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-muted ring-1 ring-edge transition-colors hover:text-ink">
          {arm.enabled ? 'Désactiver' : 'Activer'}
        </button>
      )}
    </div>
  )
}

export function LiquidOverview({ summary }: { summary: PredictionSummary }) {
  const cfg = summary.config
  const [token, setToken] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  // Aucune modification possible sur l'exemple fictif : ce ne sont pas de vrais bras.
  const editable = !summary.exemple_fictif
  const toggle = async (arm: PredictionArm) => {
    const error = await sendRequest({ action: 'toggle', arm: arm.arm, enabled: !arm.enabled }, token)
    setNotice(error ? `Refusé : ${error}`
      : `Demande déposée : ${arm.enabled ? 'désactiver' : 'activer'} ${arm.arm}. Prise en compte au prochain cycle ; les paris déjà ouverts seront réglés normalement.`)
    setReloadKey((k) => k + 1)
  }
  const domains = Object.entries(summary.marches_par_domaine).sort((a, b) => b[1] - a[1])
  return (
    <div className="grid grid-cols-1 gap-4">
      <Panel title="Comment les bras décident"
        hint="Chaque marché est rangé dans un domaine. Chaque bras n’a le droit de parier que sur ses domaines, avec une seule règle. Un fork ne change qu’une variable par rapport à son parent : c’est ce qui rend la comparaison lisible.">
        <ol className="grid grid-cols-1 gap-3 px-1 text-[11px] leading-snug text-muted sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['1 · Relever', 'Toutes les minutes : la liste des marchés, leurs prix, les carnets utiles. Un relevé de tous les prix est gardé toutes les 15 minutes.'],
            ['2 · Classer', 'Prix, sport, questions. Un modèle de marché inconnu part en « non classé » et n’est jamais parié.'],
            ['3 · Décider', cfg.ordre_min_usd
              ? `Chaque bras joue son compte de ${cfg.capital_fictif_usd} $ comme le vrai : parts entières, ordre minimum ${cfg.ordre_min_usd} $, solde suffisant, carnet partagé entre bras. Entrée au prix réel du carnet vendeur.`
              : 'Chaque bras applique sa règle. Entrée simulée au prix réel du carnet vendeur, refusée si le carnet est trop loin du prix affiché.'],
            ['4 · Régler', `Frais ${(cfg.frais_pct * 100).toFixed(2)} % à l’achat et ${(cfg.frais_reglement_pct * 100).toFixed(2)} % au règlement. ` + (cfg.delai_prudence_heures
              ? `Issue illisible ${cfg.delai_prudence_heures} h après l’échéance = comptée perdue, par prudence.`
              : 'Issue douteuse = « indéterminé », hors de tous les chiffres.')],
          ].map(([title, text]) => (
            <li key={title} className="rounded-md px-3 py-2.5 ring-1 ring-edge/50">
              <div className="font-mono text-[10px] uppercase tracking-wider text-tide">{title}</div>
              <div className="mt-1">{text}</div>
            </li>
          ))}
        </ol>
      </Panel>

      {notice && (
        <div role="status" className="rounded-md px-3 py-2 text-[11px] text-muted ring-1 ring-tide/40">{notice}</div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        {FAMILLES.map((famille) => {
          const arms = summary.arms.filter((a) => (a.famille ?? 'transverse') === famille.id)
          return (
            <Panel key={famille.id} title={famille.label} count={arms.length} hint={famille.what}>
              {arms.length === 0 ? <Empty>Aucun bras dans cette famille.</Empty> : (
                <div className="grid grid-cols-1 gap-2">
                  {arms.map((arm) => (
                    <ArmCard key={arm.arm} arm={arm} need={cfg.min_paris_verdict} onToggle={editable ? toggle : null} />
                  ))}
                </div>
              )}
            </Panel>
          )
        })}
      </div>

      {editable && (
        <LiquidActions arms={summary.arms} token={token} setToken={setToken}
          reloadKey={reloadKey} onSent={() => setReloadKey((k) => k + 1)} />
      )}

      <Panel title="Marchés ouverts par domaine" hint="Vus au dernier cycle de la simulation.">
        {domains.length === 0 ? <Empty>Aucun cycle enregistré.</Empty> : (
          <div className="grid grid-cols-2 gap-4 px-1 sm:grid-cols-4 xl:grid-cols-8">
            {domains.map(([domain, count]) => (
              <Stat key={domain} label={DOMAIN_LABEL[domain] ?? domain} value={String(count)} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}
