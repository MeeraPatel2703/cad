import { useState, useMemo } from 'react'
import { ArrowUpDown, Download, Filter, AlertTriangle } from 'lucide-react'

const STATUS_STYLES = {
  pass: 'text-success bg-success/5',
  fail: 'text-critical bg-critical/8',
  warning: 'text-warning bg-warning/6',
  deviation: 'text-sky-400 bg-sky-400/5',  // Blue for intentional customizations
  missing: 'text-purple-400 bg-purple-400/8',  // Purple for missing from check
  not_found: 'text-text-muted bg-bg-card',
  pending: 'text-text-muted bg-bg-card',
}

const STATUS_LABELS = {
  pass: 'PASS',
  fail: 'FAIL',
  warning: 'WARN',
  deviation: 'DEV',  // Intentional deviation
  missing: 'MISS',   // Missing from check drawing
  not_found: 'N/F',
  pending: '---',
}

const FILTERS = ['all', 'pass', 'fail', 'warning', 'deviation', 'missing', 'not_found']

function formatNum(val) {
  if (val === null || val === undefined) return '--'
  return Number(val).toFixed(3)
}

function formatTol(val) {
  if (val === null || val === undefined) return '--'
  const n = Number(val)
  return n >= 0 ? `+${n.toFixed(3)}` : n.toFixed(3)
}

export default function InspectionTable({
  items = [],
  selectedBalloon = null,
  onRowClick,
  summary = null,
}) {
  const [filter, setFilter] = useState('all')
  const [sortKey, setSortKey] = useState('balloon_number')
  const [sortAsc, setSortAsc] = useState(true)

  const filteredItems = useMemo(() => {
    let filtered = filter === 'all' ? items : items.filter((i) => i.status === filter)
    return [...filtered].sort((a, b) => {
      const aVal = a[sortKey] ?? ''
      const bVal = b[sortKey] ?? ''
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortAsc ? aVal - bVal : bVal - aVal
      }
      return sortAsc
        ? String(aVal).localeCompare(String(bVal))
        : String(bVal).localeCompare(String(aVal))
    })
  }, [items, filter, sortKey, sortAsc])

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  const exportCSV = () => {
    const headers = ['#', 'Feature', 'Zone', 'Nominal', 'Tol+', 'Tol-', 'Class', 'Actual', 'Deviation', 'Status']
    const rows = items.map((i) => [
      i.balloon_number,
      i.feature_description,
      i.zone || '',
      formatNum(i.master_nominal),
      formatTol(i.master_upper_tol),
      formatTol(i.master_lower_tol),
      i.master_tolerance_class || '',
      formatNum(i.check_actual),
      formatNum(i.deviation),
      i.status,
    ])
    const csv = [headers, ...rows].map((r) => r.map((c) => `"${c}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'inspection_report.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const SortHeader = ({ label, field, className = '' }) => (
    <th
      className={`px-3 py-2 text-left text-[10px] uppercase tracking-wider text-text-muted cursor-pointer hover:text-text-secondary transition-colors select-none ${className}`}
      onClick={() => toggleSort(field)}
    >
      <span className="flex items-center gap-1">
        {label}
        {sortKey === field && <ArrowUpDown size={10} className="text-accent" />}
      </span>
    </th>
  )

  return (
    <div className="flex flex-col border border-border rounded-lg bg-bg-panel overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-bg-card">
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
            Inspection Table
          </span>
          {summary && (
            <span className="text-[10px] text-text-muted">
              {summary.total_dimensions} dims
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Filters */}
          <div className="flex items-center gap-1">
            {FILTERS.map((f) => {
              const count = f === 'all' ? items.length : items.filter((i) => i.status === f).length
              if (f !== 'all' && count === 0) return null
              return (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`px-2 py-0.5 text-[10px] rounded transition-all ${
                    filter === f
                      ? 'bg-accent/15 text-accent'
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                >
                  {f === 'all' ? 'All' : f === 'not_found' ? 'N/F' : f.charAt(0).toUpperCase() + f.slice(1)}
                  <span className="ml-1 opacity-60">{count}</span>
                </button>
              )
            })}
          </div>

          <button
            onClick={exportCSV}
            className="flex items-center gap-1 px-2 py-1 text-[10px] text-text-muted hover:text-accent rounded transition-colors"
          >
            <Download size={12} />
            CSV
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-auto max-h-[350px]">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-bg-card z-10">
            <tr className="border-b border-border">
              <SortHeader label="#" field="balloon_number" className="w-10" />
              <SortHeader label="Feature" field="feature_description" />
              <SortHeader label="Zone" field="zone" className="w-24" />
              <SortHeader label="Nominal" field="master_nominal" className="w-20 text-right" />
              <th className="px-3 py-2 text-right text-[10px] uppercase tracking-wider text-text-muted w-16">Tol+</th>
              <th className="px-3 py-2 text-right text-[10px] uppercase tracking-wider text-text-muted w-16">Tol-</th>
              <SortHeader label="Actual" field="check_actual" className="w-20 text-right" />
              <SortHeader label="Dev" field="deviation" className="w-20 text-right" />
              <SortHeader label="Status" field="status" className="w-16" />
            </tr>
          </thead>
          <tbody>
            {filteredItems.map((item) => {
              const isSelected = selectedBalloon === item.balloon_number
              return (
                <tr
                  key={item.balloon_number}
                  onClick={() => onRowClick?.(item.balloon_number)}
                  className={`border-b border-border/50 cursor-pointer transition-all hover:bg-bg-hover ${
                    isSelected ? 'inspection-row-selected' : ''
                  } ${STATUS_STYLES[item.status] || ''}`}
                >
                  <td className="px-3 py-1.5 font-bold text-text-secondary">{item.balloon_number}</td>
                  <td className="px-3 py-1.5 text-text-primary truncate max-w-[200px]">{item.feature_description}</td>
                  <td className="px-3 py-1.5 text-text-muted">{item.zone || '--'}</td>
                  <td className="px-3 py-1.5 text-right text-text-primary tabular-nums">
                    <span className="inline-flex items-center gap-1 justify-end">
                      {formatNum(item.master_nominal)}
                      {item.master_ocr_verified === false && (
                        <AlertTriangle size={10} className="text-warning shrink-0" title="OCR could not verify this value in the master drawing" />
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right text-text-muted tabular-nums">{formatTol(item.master_upper_tol)}</td>
                  <td className="px-3 py-1.5 text-right text-text-muted tabular-nums">{formatTol(item.master_lower_tol)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {item.status === 'missing'
                      ? <span className="text-purple-400 italic text-[10px]">Not found</span>
                      : <span className="inline-flex items-center gap-1 justify-end text-text-primary">
                          {formatNum(item.check_actual)}
                          {item.check_ocr_verified === false && (
                            <AlertTriangle size={10} className="text-warning shrink-0" title="OCR could not verify this value in the check drawing" />
                          )}
                        </span>
                    }
                  </td>
                  <td className={`px-3 py-1.5 text-right tabular-nums ${
                    item.status === 'fail' ? 'text-critical font-bold' :
                    item.status === 'warning' ? 'text-warning' :
                    item.status === 'deviation' ? 'text-sky-400' :
                    item.status === 'missing' ? 'text-purple-400' : 'text-text-secondary'
                  }`}>
                    {item.status === 'missing' ? '--' : formatNum(item.deviation)}
                  </td>
                  <td className="px-3 py-1.5">
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-bold tracking-wider ${
                      item.status === 'pass' ? 'bg-success/15 text-success' :
                      item.status === 'fail' ? 'bg-critical/15 text-critical' :
                      item.status === 'warning' ? 'bg-warning/15 text-warning' :
                      item.status === 'deviation' ? 'bg-sky-400/15 text-sky-400' :
                      item.status === 'missing' ? 'bg-purple-400/15 text-purple-400' :
                      'bg-bg-hover text-text-muted'
                    }`}>
                      {STATUS_LABELS[item.status] || item.status}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Summary footer */}
      {summary && (
        <div className="flex items-center gap-4 px-4 py-2 border-t border-border bg-bg-card text-[10px]">
          <span className="text-text-muted">
            Total: <span className="text-text-secondary">{summary.total_dimensions}</span>
          </span>
          <span className="text-success">
            Pass: {summary.pass}
          </span>
          <span className="text-critical">
            Fail: {summary.fail}
          </span>
          <span className="text-warning">
            Warn: {summary.warning}
          </span>
          <span className="text-sky-400">
            Dev: {summary.deviation || 0}
          </span>
          <span className="text-purple-400">
            Missing: {summary.missing || 0}
          </span>
          {summary.bom_mismatches > 0 && (
            <span className="text-orange-400">
              BOM: {summary.bom_mismatches}
            </span>
          )}
          <span className="ml-auto text-text-secondary">
            Score: <span className={`font-bold ${
              summary.score >= 80 ? 'text-success' :
              summary.score >= 50 ? 'text-warning' : 'text-critical'
            }`}>{summary.score}%</span>
          </span>
        </div>
      )}
    </div>
  )
}
