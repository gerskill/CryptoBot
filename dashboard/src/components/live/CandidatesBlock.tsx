import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, Empty } from '../Panel'
import { CandidateRow } from './PriceRow'

export function CandidatesBlock() {
  const candidates = useStore((s) => s.state.candidates ?? EMPTY_ARRAY)

  return (
    <Panel title="Candidats du cycle" count={candidates.length}>
      {candidates.length === 0 ? (
        <Empty>Aucun candidat retenu ce cycle.</Empty>
      ) : (
        <ul className="space-y-1.5">
          {candidates.map((candidate) => (
            <CandidateRow key={candidate.token_address} candidate={candidate} />
          ))}
        </ul>
      )}
    </Panel>
  )
}
