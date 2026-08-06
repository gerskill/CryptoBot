import { motion } from 'framer-motion'
import { usd } from '../../lib/format'
import type { CurveColor } from '../../lib/armColors'

export type CurveSeries = {
  arm: string
  /** Valeurs cumulées, point de départ (0) inclus. */
  points: number[]
  color: CurveColor
}

type Props = {
  curves: CurveSeries[]
  visible: Set<string>
  highlighted: string | null
  /** Courbe agrégée (`arm=all`), tracée en pointillés comme référence. */
  aggregate?: number[]
}

/** P&L cumulé multi-bras. SVG 0-100 commun, échelle dynamique sur le visible. */
export function MultiCurveChart({ curves, visible, highlighted, aggregate }: Props) {
  const visibleCurves = curves.filter((curve) => visible.has(curve.arm))

  if (visibleCurves.length === 0) {
    return (
      <p className="px-1 py-6 text-center text-[11px] text-dim">
        Aucun bras affiché — coche une stratégie ci-dessus.
      </p>
    )
  }

  const allPoints = visibleCurves.flatMap((curve) => curve.points).concat(aggregate ?? [])
  if (allPoints.length < 2) {
    return <p className="px-1 py-6 text-center text-[11px] text-dim">Historique insuffisant.</p>
  }

  const min = Math.min(...allPoints)
  const max = Math.max(...allPoints)
  const span = max - min || 1

  const buildPath = (points: number[]): string =>
    points
      .map((value, index) => {
        const x = points.length > 1 ? (index / (points.length - 1)) * 100 : 0
        const y = 100 - ((value - min) / span) * 100
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(' ')

  const finalValues = visibleCurves.map((curve) => curve.points[curve.points.length - 1] ?? 0)
  const finalLabel = Math.max(...finalValues)

  return (
    <div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-40 w-full overflow-visible">
        {aggregate && aggregate.length > 1 && (
          <path
            d={buildPath(aggregate)}
            fill="none"
            stroke="var(--color-dim)"
            strokeWidth="1"
            strokeDasharray="2 1"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {visibleCurves.map((curve) => {
          const dimmed = highlighted !== null && highlighted !== curve.arm
          return (
            <motion.path
              key={curve.arm}
              d={buildPath(curve.points)}
              fill="none"
              stroke={curve.color.stroke}
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
              initial={false}
              animate={{ opacity: dimmed ? 0.2 : 1 }}
              transition={{ duration: 0.25 }}
            />
          )
        })}
      </svg>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-dim tabular-nums">
        <span>$0</span>
        <span>{usd(finalLabel, 0)}</span>
      </div>
    </div>
  )
}
