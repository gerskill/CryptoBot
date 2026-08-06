import { useEffect, useState } from 'react'
import { useStore } from '../../lib/store'
import { PositionBlock } from './PositionBlock'
import { CandidatesBlock } from './CandidatesBlock'

/** Secondes écoulées depuis `timestamp` (epoch secondes), retiquée localement.
 *  Un seul appelant : inline plutôt qu'un fichier de hook séparé. */
function useSecondsSince(timestamp: number | undefined): number | null {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  if (!timestamp) return null
  return Math.max(0, Math.round(now / 1000 - timestamp))
}

/** Vue temps réel : zéro fetch, tout vient déjà du store poussé par le WS. */
export function LivePrices() {
  const connected = useStore((s) => s.connected)
  const positionsCount = useStore((s) => s.state.positions?.length ?? 0)
  const candidatesCount = useStore((s) => s.state.candidates?.length ?? 0)
  const updatedAt = useStore((s) => s.state.updated_at)
  const secondsAgo = useSecondsSince(updatedAt)

  return (
    <div>
      {!connected && (
        <div className="mb-4 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2.5 text-[11px] text-warn">
          Connexion perdue — les prix affichés datent du dernier push reçu, pas du marché actuel.
        </div>
      )}

      <div className="mb-3 font-mono text-[10px] text-dim">
        {positionsCount} position{positionsCount > 1 ? 's' : ''} · {candidatesCount} candidat
        {candidatesCount > 1 ? 's' : ''}
        {secondsAgo != null && <> · mis à jour il y a {secondsAgo}s</>}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <PositionBlock />
        <CandidatesBlock />
      </div>
    </div>
  )
}
