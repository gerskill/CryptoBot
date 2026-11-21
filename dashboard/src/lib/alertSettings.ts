import type { AlertSettings } from './alerts'

const STORAGE_KEY = 'cryptobot.alerts.v1'
const MIN_LOSS_PCT = 0.5
const MAX_LOSS_PCT = 100

export const DEFAULT_SETTINGS: AlertSettings = { enabled: false, lossPct: 5, sound: false }

/** Réglages venus du stockage : jamais de confiance, valeurs bornées. */
export function sanitizeSettings(raw: unknown): AlertSettings {
  if (typeof raw !== 'object' || raw === null) return DEFAULT_SETTINGS
  const input = raw as Record<string, unknown>
  const loss = typeof input.lossPct === 'number' && Number.isFinite(input.lossPct) ? input.lossPct : null
  return {
    enabled: input.enabled === true,
    lossPct: loss === null ? DEFAULT_SETTINGS.lossPct : Math.min(MAX_LOSS_PCT, Math.max(MIN_LOSS_PCT, loss)),
    sound: input.sound === true,
  }
}

/** Le stockage peut échouer (navigation privée, quota) : on retombe sur les défauts. */
export function loadSettings(): AlertSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored ? sanitizeSettings(JSON.parse(stored)) : DEFAULT_SETTINGS
  } catch {
    return DEFAULT_SETTINGS
  }
}

export function saveSettings(settings: AlertSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch {
    /* mémorisation impossible : les réglages restent valables pour la session */
  }
}
