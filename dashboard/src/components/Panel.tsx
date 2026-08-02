import type { ReactNode } from 'react'

/**
 * Carte de base.
 *
 * Deux changements par rapport à la version précédente, tous deux au service
 * de la lisibilité à quatre colonnes :
 *
 * - L'en-tête porte un FILET d'accent plutôt qu'une bordure complète. Quatre
 *   panneaux entièrement encadrés produisent huit lignes verticales qui
 *   découpent l'écran ; un filet suffit à séparer et laisse respirer.
 * - `figure` permet de sortir UN chiffre du corps. Sans ça, la donnée qui
 *   compte a la même taille que celle qui ne compte pas.
 */
export function Panel({
  title,
  count,
  figure,
  figureTone,
  hint,
  children,
  className = '',
}: {
  title: string
  count?: number | string
  /** Le chiffre qui résume le panneau. Un seul, sinon plus rien ne domine. */
  figure?: string
  figureTone?: 'neutral' | 'gain' | 'loss'
  hint?: string
  children: ReactNode
  className?: string
}) {
  const tone =
    figureTone === 'gain' ? 'text-toxic' : figureTone === 'loss' ? 'text-blood' : 'text-ink'

  return (
    <section
      className={`flex min-h-0 flex-col rounded-lg bg-surface/70 ring-1 ring-edge/60 ${className}`}
    >
      <header className="shrink-0 px-4 pt-3.5">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-[10px] font-semibold uppercase tracking-[0.2em] text-dim">
            {title}
          </h2>
          {count !== undefined && (
            <span className="font-mono text-[10px] text-dim">{count}</span>
          )}
        </div>
        {figure !== undefined && (
          <div className={`mt-1 font-mono text-[1.75rem] leading-none ${tone}`}>{figure}</div>
        )}
        {/* Filet d'accent : sépare sans encadrer. */}
        <div className="mt-3 h-px bg-gradient-to-r from-edge-strong via-edge/40 to-transparent" />
      </header>
      {hint && (
        <p className="shrink-0 px-4 pt-2.5 text-[11px] leading-snug text-muted">{hint}</p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">{children}</div>
    </section>
  )
}

/**
 * Donnée sans source — jamais un zéro trompeur.
 *
 * Le trait pointillé est délibéré : il signale visuellement « absence » là où
 * un cadre plein signalerait « valeur ». Un 0 affiché comme une mesure est un
 * mensonge ; ce composant existe pour rendre le manque visible.
 */
export function NoSource({ label, why }: { label: string; why: string }) {
  return (
    <div className="rounded-md border border-dashed border-edge px-3 py-2">
      <div className="text-[11px] font-medium text-muted">{label}</div>
      <div className="mt-0.5 text-[10px] leading-snug text-dim">{why}</div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-[100px] items-center justify-center px-5 text-center text-[11px] leading-relaxed text-dim">
      {children}
    </div>
  )
}

/** Étiquette + valeur, alignées. L'unité de base de toute lecture chiffrée. */
export function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: string
  tone?: 'neutral' | 'gain' | 'loss' | 'warn'
  hint?: string
}) {
  const color =
    tone === 'gain'
      ? 'text-toxic'
      : tone === 'loss'
        ? 'text-blood'
        : tone === 'warn'
          ? 'text-warn'
          : 'text-ink'
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
      <div className={`mt-0.5 font-mono text-[15px] leading-none ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-[10px] leading-snug text-dim">{hint}</div>}
    </div>
  )
}
