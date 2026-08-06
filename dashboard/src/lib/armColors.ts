/** 7 couleurs fixes dérivées du design system pour les courbes multi-bras —
 *  jamais de couleur random. Fonction pure, aucun import React. */
export type CurveColor = { stroke: string; fill: string }

const ARM_COLOR_PALETTE: CurveColor[] = [
  { stroke: 'var(--color-toxic)', fill: 'rgba(46, 229, 107, 0.12)' },
  { stroke: 'var(--color-blood)', fill: 'rgba(255, 74, 110, 0.12)' },
  { stroke: 'var(--color-warn)', fill: 'rgba(255, 171, 46, 0.12)' },
  { stroke: 'var(--color-gem)', fill: 'rgba(124, 108, 255, 0.12)' },
  { stroke: 'var(--color-muted)', fill: 'rgba(139, 141, 155, 0.12)' },
  { stroke: 'var(--color-ink)', fill: 'rgba(237, 238, 242, 0.12)' },
  { stroke: 'rgba(90, 92, 106, 0.5)', fill: 'rgba(90, 92, 106, 0.08)' },
]

export function armColor(index: number): CurveColor {
  return ARM_COLOR_PALETTE[index % ARM_COLOR_PALETTE.length]
}
