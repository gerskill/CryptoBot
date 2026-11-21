import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  JOURNAL_MAX, appendEntry, describeFailure, entryStatus, parseJournal, sendBotCommand,
  type JournalEntry,
} from './pilotage'

const entry = (n: number, over: Partial<JournalEntry> = {}): JournalEntry => ({
  id: `e${n}`, t: 1_000_000 + n, label: `cmd ${n}`, kind: 'bot', ok: true, detail: 'ok', ...over,
})

function reponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('describeFailure', () => {
  it('dit quoi, pourquoi et que faire quand l’API est injoignable', () => {
    const msg = describeFailure(null)
    expect(msg).toMatch(/non envoyée/)
    expect(msg).toMatch(/injoignable/)
    expect(msg).toMatch(/réessayer/)
  })
  it('401 : jeton requis', () => {
    expect(describeFailure(401, 'jeton invalide ou absent')).toMatch(/DASHBOARD_TOKEN/)
  })
  it('422 : relaie le détail du serveur', () => {
    expect(describeFailure(422, 'action inconnue : stop ou resume seulement')).toMatch(/action inconnue/)
  })
  it('429 : trop de demandes en attente', () => {
    expect(describeFailure(429, 'trop de demandes en attente')).toMatch(/attente/)
  })
  it('5xx : erreur serveur sans inventer de cause', () => {
    expect(describeFailure(500)).toMatch(/HTTP 500/)
  })
})

describe('sendBotCommand', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { vi.restoreAllMocks() })

  it('envoie stop avec l’en-tête X-CryptobBot, le type JSON et le jeton en Bearer', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(200, { ok: true, id: 'x', note: 'arrêt transmis' }))
    const out = await sendBotCommand('stop', 'secret')
    expect(out).toEqual({ ok: true, detail: 'arrêt transmis' })
    const [url, init] = spy.mock.calls[0]
    expect(url).toBe('/api/bot/commandes')
    const headers = (init as RequestInit).headers as Record<string, string>
    expect(headers['X-CryptobBot']).toBe('1')
    expect(headers['Content-Type']).toBe('application/json')
    expect(headers.Authorization).toBe('Bearer secret')
    expect((init as RequestInit).body).toBe(JSON.stringify({ action: 'stop' }))
  })

  it('sans jeton : pas d’en-tête Authorization (accès local inchangé)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(200, { ok: true, note: 'n' }))
    await sendBotCommand('resume', '')
    const headers = (spy.mock.calls[0][1] as RequestInit).headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
  })

  it('refus du serveur : ok=false avec explication', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(401, { detail: 'jeton invalide ou absent' }))
    const out = await sendBotCommand('stop', '')
    expect(out.ok).toBe(false)
    expect(out.detail).toMatch(/DASHBOARD_TOKEN/)
  })

  it('réseau coupé : ok=false, jamais d’exception', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const out = await sendBotCommand('stop', '')
    expect(out.ok).toBe(false)
    expect(out.detail).toMatch(/injoignable/)
  })

  it('refuse une action hors liste sans appeler le réseau', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    // @ts-expect-error action volontairement invalide
    const out = await sendBotCommand('buy', '')
    expect(out.ok).toBe(false)
    expect(spy).not.toHaveBeenCalled()
  })

  it('ne recopie jamais le jeton dans le résultat', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(401, { detail: 'jeton invalide ou absent' }))
    const out = await sendBotCommand('stop', 'TOP-SECRET')
    expect(JSON.stringify(out)).not.toContain('TOP-SECRET')
  })
})

describe('journal', () => {
  it('ajoute en tête sans muter la liste d’origine', () => {
    const base = [entry(1)]
    const next = appendEntry(base, entry(2))
    expect(next.map((e) => e.id)).toEqual(['e2', 'e1'])
    expect(base).toHaveLength(1)
  })
  it('borne la longueur', () => {
    let list: JournalEntry[] = []
    for (let i = 0; i < JOURNAL_MAX + 5; i++) list = appendEntry(list, entry(i))
    expect(list).toHaveLength(JOURNAL_MAX)
    expect(list[0].id).toBe(`e${JOURNAL_MAX + 4}`)
  })
  it('parseJournal ignore le contenu mal formé', () => {
    expect(parseJournal('pas du json')).toEqual([])
    expect(parseJournal(null)).toEqual([])
    expect(parseJournal(JSON.stringify([{ id: 1 }, entry(1), 'x']))).toEqual([entry(1)])
  })
  it('parseJournal tronque un détail trop long', () => {
    const [e] = parseJournal(JSON.stringify([entry(1, { detail: 'x'.repeat(900) })]))
    expect(e.detail.length).toBeLessThanOrEqual(300)
  })
})

describe('entryStatus', () => {
  it('refusée', () => {
    expect(entryStatus(entry(1, { ok: false }), 1_000_000 + 10).tone).toBe('refusee')
  })
  it('commande bot déposée récemment : transmise', () => {
    const s = entryStatus(entry(1, { t: 1_000_000 }), 1_000_000 + 60_000)
    expect(s.tone).toBe('transmise')
  })
  it('commande bot déposée depuis plus de 15 min : périmée possible, à renvoyer', () => {
    const s = entryStatus(entry(1, { t: 1_000_000 }), 1_000_000 + 16 * 60_000)
    expect(s.tone).toBe('perimee')
    expect(s.text).toMatch(/15 min/)
  })
})
