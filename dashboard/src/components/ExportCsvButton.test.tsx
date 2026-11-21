import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ExportCsvButton } from './ExportCsvButton'
import * as download from '../lib/downloadCsv'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const columns = [{ header: 'nom', value: (r: { n: string }) => r.n }]

describe('ExportCsvButton', () => {
  it('télécharge un CSV daté avec les lignes affichées', () => {
    const spy = vi.spyOn(download, 'downloadCsv').mockReturnValue(true)
    render(<ExportCsvButton rows={[{ n: '=evil' }]} columns={columns} kind="trades" label="Exporter" />)
    fireEvent.click(screen.getByRole('button', { name: /Exporter \(1\)/ }))
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0]).toMatch(/^cryptobot-trades-\d{4}-\d{2}-\d{2}\.csv$/)
    expect(spy.mock.calls[0][1]).toBe("nom\r\n'=evil\r\n")
  })
  it('désactivé sans ligne', () => {
    render(<ExportCsvButton rows={[]} columns={columns} kind="trades" label="Exporter" />)
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true)
  })
  it('signale un refus du navigateur', () => {
    vi.spyOn(download, 'downloadCsv').mockReturnValue(false)
    render(<ExportCsvButton rows={[{ n: 'a' }]} columns={columns} kind="trades" label="Exporter" />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText(/refusé/)).toBeTruthy()
  })
})
