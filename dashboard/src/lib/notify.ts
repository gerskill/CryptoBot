/** API Notification et son : seule frontière impure des alertes. */

export type NotificationState = 'granted' | 'denied' | 'default' | 'unsupported'

export function notificationState(): NotificationState {
  if (typeof Notification === 'undefined') return 'unsupported'
  return Notification.permission
}

/** À n'appeler QUE depuis un clic : jamais de demande au chargement. */
export async function requestNotificationPermission(): Promise<NotificationState> {
  if (typeof Notification === 'undefined') return 'unsupported'
  try {
    return await Notification.requestPermission()
  } catch {
    return notificationState()
  }
}

export function showSystemNotification(message: { title: string; body: string; tag: string }): boolean {
  if (notificationState() !== 'granted') return false
  try {
    new Notification(message.title, { body: message.body, tag: message.tag })
    return true
  } catch {
    return false
  }
}

const BEEP_HZ = 660
const BEEP_SECONDS = 0.18

/** Bip bref (désactivé par défaut côté réglages). Silencieux si AudioContext est refusé. */
export function playBeep(): void {
  try {
    const context = new AudioContext()
    const oscillator = context.createOscillator()
    const gain = context.createGain()
    oscillator.frequency.value = BEEP_HZ
    gain.gain.value = 0.05
    oscillator.connect(gain).connect(context.destination)
    oscillator.start()
    oscillator.stop(context.currentTime + BEEP_SECONDS)
    oscillator.onended = () => void context.close()
  } catch {
    /* pas de son : l'alerte reste visible */
  }
}

/** `(2) Titre` quand des alertes sont non lues, le titre d'origine sinon. */
export function tabTitle(baseTitle: string, unread: number): string {
  return unread > 0 ? `(${unread}) ${baseTitle}` : baseTitle
}
