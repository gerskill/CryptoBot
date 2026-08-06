import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, Empty } from '../Panel'
import { PositionRow } from './PriceRow'

export function PositionBlock() {
  const positions = useStore((s) => s.state.positions ?? EMPTY_ARRAY)

  return (
    <Panel title="Positions ouvertes" count={positions.length}>
      {positions.length === 0 ? (
        <Empty>Aucune position ouverte. Le bot scanne.</Empty>
      ) : (
        <ul className="space-y-1.5">
          {positions.map((pos) => (
            <PositionRow key={pos.id} position={pos} />
          ))}
        </ul>
      )}
    </Panel>
  )
}
