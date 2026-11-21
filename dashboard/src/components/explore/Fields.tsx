import type { ReactNode } from 'react'

/** [libellé, valeur, classe de couleur optionnelle]. */
export type FieldRow = readonly [string, ReactNode, string?]

/**
 * Liste libellé / valeur d'un tiroir.
 *
 * Une valeur absente (`null`, `undefined`, texte vide) ne produit AUCUNE
 * ligne : on n'affiche pas un « 0 » ou un « — » à la place d'un champ que
 * l'API n'a pas fourni. Un zéro réel, lui, s'affiche.
 */
export function Fields({ title, rows }: { title?: string; rows: readonly FieldRow[] }) {
  const present = rows.filter(([, value]) => value != null && value !== '' && value !== false)
  if (present.length === 0) return null
  return (
    <section>
      {title && (
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-dim">{title}</h3>
      )}
      <dl className="space-y-1.5">
        {present.map(([label, value, tone]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 text-xs">
            <dt className="shrink-0 text-dim">{label}</dt>
            <dd className={`min-w-0 break-words text-right font-mono tabular-nums ${tone ?? 'text-ink'}`}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
