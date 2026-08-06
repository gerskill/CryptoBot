import { useState } from 'react'

type Props = {
  onSubmit: (token: string) => void
}

/** Le jeton reste en `useState` local — jamais dans le store, jamais dans localStorage. */
export function TokenGate({ onSubmit }: Props) {
  const [value, setValue] = useState('')

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit(value)
      }}
      className="mx-auto max-w-xs px-4 py-6 text-center"
    >
      <p className="text-[11px] leading-snug text-dim">
        Ce bras nécessite un jeton — <code className="text-muted">DASHBOARD_TOKEN</code>
      </p>
      <input
        type="password"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="jeton"
        autoComplete="off"
        className="mt-3 w-full rounded-md bg-void px-3 py-2 font-mono text-xs text-ink outline-none ring-1 ring-edge/60 focus:ring-gem/60"
      />
      <button
        type="submit"
        className="mt-2 w-full rounded-md px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-dim ring-1 ring-edge/60 transition-colors hover:text-ink hover:ring-edge-strong"
      >
        Valider
      </button>
    </form>
  )
}
