import { BotControls } from './BotControls'
import { PilotageJournal } from './PilotageJournal'

/** Onglet « Pilotage » de l'espace memecoins : commandes puis journal. */
export function PilotageView() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <BotControls />
      <PilotageJournal />
    </div>
  )
}
