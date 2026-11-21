import type { PredictionBet } from '../../lib/predictionTypes'
import { useBetPrices } from '../../lib/usePrediction'
import { pnlColor, utc, prob, usd } from '../../lib/format'
import { BetChart } from '../liquid/BetChart'
import { DOMAIN_LABEL, signed } from '../liquid/liquidLabels'
import { issueLabel } from '../liquid/betSort'
import { Fields } from './Fields'

/** Contenu du tiroir d'un pari : champs du journal + courbe si le prix est servi. */
export function BetDetail({ bet, demo }: { bet: PredictionBet; demo: boolean }) {
  const prices = useBetPrices(bet.coin, demo)
  const settled = bet.status === 'regle'
  return (
    <>
      <Fields
        title="Pari"
        rows={[
          ['Marché', bet.title],
          ['Côté', bet.side_name],
          ['Domaine', DOMAIN_LABEL[bet.domain] ?? bet.domain],
          ['Lieu', bet.venue],
          ['Pièce', bet.coin],
          ['Issue', issueLabel(bet)],
          ['Réglé par prudence', bet.regle_par_prudence ? 'oui' : null],
        ]}
      />
      <Fields
        title="Prix et résultat"
        rows={[
          ['Entrée', utc(bet.entry_time)],
          ['Prix payé', prob(bet.entry_price)],
          ['Probabilité estimée', bet.prob_estimee != null ? prob(bet.prob_estimee) : null],
          ['Source de l’estimation', bet.source_estimation],
          ['Mise', usd(bet.stake_usd, 2)],
          ['Parts', bet.shares],
          ['Échéance', bet.expiry ? utc(bet.expiry) : null],
          ['Règlement', settled && bet.settle_time ? utc(bet.settle_time) : null],
          ['Valeur de règlement', settled && bet.settle_value != null ? bet.settle_value : null],
          ['P&L net', bet.pnl_net != null ? signed(bet.pnl_net) : null, bet.pnl_net != null ? pnlColor(bet.pnl_net) : undefined],
        ]}
      />
      {prices.points.length > 0 && (
        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-dim">Courbe du prix</h3>
          <BetChart bet={bet} points={prices.points} />
        </section>
      )}
      {prices.error && <p className="text-[11px] text-blood">Prix illisibles : {prices.error}</p>}
    </>
  )
}
