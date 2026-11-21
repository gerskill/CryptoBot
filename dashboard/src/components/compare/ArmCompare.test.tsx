import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArmCompare } from './ArmCompare'
import { useStore } from '../../lib/store'
import type { Arm } from '../../lib/types'

const mkArm = (name: string): Arm => ({ name, enabled: true, stats: { baseline: 1000 } }) as unknown as Arm
const recent = new Date(Date.now() - 86_400_000).toISOString()
const old = new Date(Date.now() - 20 * 86_400_000).toISOString()

beforeEach(() => {
  useStore.setState({ state: { bot_online: true, arms: ['a', 'b', 'c', 'd', 'e'].map(mkArm) } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      json: async () => ({
        trades: [
          { timestamp_exit: old, pnl_usd: -40 },
          { timestamp_exit: recent, pnl_usd: 25 },
        ],
        url,
      }),
    })),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ArmCompare', () => {
  it('compare 2 bras par défaut, sur 7 jours, avec tableau', async () => {
    render(<ArmCompare />)
    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy())
    expect(screen.getByText(/2 bras sélectionnés sur 4/)).toBeTruthy()
    expect(fetch).toHaveBeenCalledTimes(2)
    // 7 j : seul le trade récent (+25) compte
    expect(screen.getAllByText('$25.00').length).toBeGreaterThan(0)
  })

  it('« Tout » inclut le trade ancien, sans nouvel appel réseau', async () => {
    render(<ArmCompare />)
    await waitFor(() => screen.getByRole('table'))
    fireEvent.click(screen.getByRole('button', { name: /Tout/ }))
    await waitFor(() => expect(screen.getAllByText('$-15.00').length).toBeGreaterThan(0))
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('plafonne à 4 bras', async () => {
    render(<ArmCompare />)
    for (const name of ['c', 'd', 'e']) fireEvent.click(screen.getByRole('button', { name }))
    expect(screen.getByText(/4 bras sélectionnés sur 4/)).toBeTruthy()
  })

  it('signale une plage inversée', async () => {
    render(<ArmCompare />)
    fireEvent.click(screen.getByRole('button', { name: /Dates/ }))
    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-11-10' } })
    fireEvent.change(screen.getByLabelText(/Au \(inclus\)/), { target: { value: '2026-11-01' } })
    expect(screen.getByRole('alert').textContent).toContain('début est après')
  })
})
