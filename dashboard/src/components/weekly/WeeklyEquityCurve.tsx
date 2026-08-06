import { motion } from 'framer-motion'
import { usd, pnlColor } from '../../lib/format'
import { NoSource } from '../Panel'

type Props = {
  /** Points cumulés, point de départ inclus en position 0. */
  series: number[]
}

/** SVG identique au motif d'`EquityCurve` de TheBrain, filtré sur la semaine. */
export function WeeklyEquityCurve({ series }: Props) {
  if (series.length < 2) {
    return (
      <NoSource
        label="Courbe indisponible"
        why={`Il faut au moins 2 trades clôturés cette semaine (${Math.max(0, series.length - 1)} pour l'instant).`}
      />
    )
  }

  const min = Math.min(...series)
  const max = Math.max(...series)
  const span = max - min || 1
  const path = series
    .map((value, index) => {
      const x = (index / (series.length - 1)) * 100
      const y = 100 - ((value - min) / span) * 100
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')

  const up = series[series.length - 1] >= series[0]

  return (
    <div className="px-1 py-2">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-24 w-full">
        <motion.path
          d={path}
          fill="none"
          stroke={up ? 'var(--color-toxic)' : 'var(--color-blood)'}
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.6 }}
        />
      </svg>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-dim tabular-nums">
        <span>{usd(series[0], 0)}</span>
        <span className={pnlColor(series[series.length - 1] - series[0])}>
          {usd(series[series.length - 1], 2)}
        </span>
      </div>
    </div>
  )
}
