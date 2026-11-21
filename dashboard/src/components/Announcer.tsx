import { useStore } from '../lib/store'

/** Région `status` unique : les lecteurs d'écran y reçoivent les annonces. */
export function Announcer() {
  const message = useStore((s) => s.announcement)
  return <div role="status" aria-live="polite" className="sr-only">{message}</div>
}
