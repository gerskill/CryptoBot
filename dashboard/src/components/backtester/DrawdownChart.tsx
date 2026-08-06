import { motion } from 'framer-motion'
import type { CurveColor } from '../../lib/armColors'

export type DrawdownCurve = {
  arm: string
  /** Drawdown % à chaque point (0 = pas de drawdown). */
  points: number[]
  color: CurveColor
}

type Props = {
  curves: DrawdownCurve[]
  visible: Set<string>
  highlighted: string | null
}

/** Drawdown par bras : 0 % en haut, creux vers le bas, zone remplie sous la courbe. */
export function DrawdownChart({ curves, visible, highlighted }: Props) {
  const visibleCurves = curves.filter((curve) => visible.has(curve.arm))
  const allValues = visibleCurves.flatMap((curve) => curve.points)

  if (allValues.length < 2) {
    return <p className="px-1 py-6 text-center text-[11px] text-dim">Pas assez de données.</p>
  }

  const max = Math.max(...allValues, 1)

  const buildLine = (points: number[]): string =>
    points
      .map((value, index) => {
        const x = points.length > 1 ? (index / (points.length - 1)) * 100 : 0
        const y = (value / max) * 100
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(' ')

  const buildArea = (points: number[]): string => {
    const line = buildLine(points)
    const lastX = points.length > 1 ? 100 : 0
    return `${line} L${lastX.toFixed(2)},0 L0,0 Z`
  }

  return (
    <div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-28 w-full overflow-visible">
        {visibleCurves.map((curve) => {
          const dimmed = highlighted !== null && highlighted !== curve.arm
          return (
            <motion.g
              key={curve.arm}
              initial={false}
              animate={{ opacity: dimmed ? 0.2 : 1 }}
              transition={{ duration: 0.25 }}
            >
              <path d={buildArea(curve.points)} fill={curve.color.stroke} fillOpacity={0.1} stroke="none" />
              <path
                d={buildLine(curve.points)}
                fill="none"
                stroke={curve.color.stroke}
                strokeWidth="1.25"
                vectorEffect="non-scaling-stroke"
              />
            </motion.g>
          )
        })}
      </svg>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-dim tabular-nums">
        <span>0%</span>
        <span className="text-blood">-{max.toFixed(1)}%</span>
      </div>
    </div>
  )
}
