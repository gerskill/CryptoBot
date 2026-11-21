import { useState } from 'react'
import { csvFilename, toCsv, type CsvColumn } from '../lib/csv'
import { downloadCsv } from '../lib/downloadCsv'
import { DownloadIcon } from './alerts/icons'

type Props<T> = {
  /** Ce qui est affiché à l'écran : on exporte exactement ces lignes. */
  rows: readonly T[]
  columns: CsvColumn<T>[]
  kind: string
  label: string
}

/** Export CSV daté. Désactivé quand il n'y a rien à exporter ; dit l'échec. */
export function ExportCsvButton<T>({ rows, columns, kind, label }: Props<T>) {
  const [failed, setFailed] = useState(false)
  const onClick = () => setFailed(!downloadCsv(csvFilename(kind), toCsv(columns, rows)))

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={rows.length === 0}
        className="flex min-h-11 items-center gap-1.5 rounded-md px-3 text-[12px] text-muted ring-1 ring-edge-strong transition-colors hover:text-ink active:scale-[0.97] disabled:opacity-40"
      >
        <DownloadIcon />
        {label} ({rows.length})
      </button>
      {failed && <span className="text-[11px] text-warn">Téléchargement refusé par le navigateur.</span>}
    </div>
  )
}
