/** Carte de base : verre légèrement teinté, bord discret. */
export function Panel({
  title,
  count,
  hint,
  children,
  className = '',
}: {
  title: string
  count?: number | string
  hint?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <section
      className={`flex min-h-0 flex-col rounded-xl border border-edge/70 bg-surface/50 backdrop-blur-sm ${className}`}
    >
      <header className="flex shrink-0 items-baseline justify-between border-b border-edge/50 px-4 py-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.18em] text-dim">{title}</h2>
        {count !== undefined && (
          <span className="font-mono text-xs text-dim tabular-nums">{count}</span>
        )}
      </header>
      {hint && <p className="shrink-0 px-4 pt-2 text-[11px] leading-snug text-dim">{hint}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">{children}</div>
    </section>
  )
}

/** Affiché quand une donnée n'a aucune source — jamais un zéro trompeur. */
export function NoSource({ label, why }: { label: string; why: string }) {
  return (
    <div className="rounded-lg border border-dashed border-edge px-3 py-2.5">
      <div className="text-xs font-medium text-dim">{label}</div>
      <div className="mt-0.5 text-[11px] leading-snug text-dim/70">{why}</div>
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[120px] items-center justify-center px-4 text-center text-xs text-dim">
      {children}
    </div>
  )
}
