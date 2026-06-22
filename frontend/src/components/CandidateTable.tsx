import type { Candidate } from '../types'
import Term from './Term'

interface ColDef {
  key: keyof Candidate
  label: string
  term?: string         // 词典键（表头 ⓘ）
  num?: boolean
}

const COLUMNS: ColDef[] = [
  { key: 'symbol', label: '代码' },
  { key: 'name', label: '名称' },
  { key: 'board', label: '板块', term: '板块' },
  { key: 'status_label', label: '状态', term: '状态' },
  { key: 'pe', label: 'PE', term: 'PE', num: true },
  { key: 'pb', label: 'PB', term: 'PB', num: true },
  { key: 'roe', label: 'ROE', term: 'ROE', num: true },
  { key: 'revenue_yoy', label: '营收同比', term: '营收同比', num: true },
  { key: 'mom_60', label: '动量(60)', term: '动量', num: true },
  { key: 'score_value', label: '价值分', term: '多因子综合得分', num: true },
  { key: 'score_quality', label: '质量分', term: '多因子综合得分', num: true },
  { key: 'total_score', label: '综合得分', term: '多因子综合得分', num: true },
]

function fmt(v: string | number | null, num?: boolean): string {
  if (v === null || v === undefined) return '—'
  if (num && typeof v === 'number') return v.toFixed(2)
  return String(v)
}

function statusClass(s: string | null): string {
  if (s === 'ST') return 'tag tag-st'
  if (s === '退市') return 'tag tag-delisted'
  return 'tag tag-normal'
}

interface Props {
  rows: Candidate[]
  selected: string | null
  onSelect: (symbol: string) => void
}

export default function CandidateTable({ rows, selected, onSelect }: Props) {
  return (
    <div className="table-wrap">
      <table className="candidates">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th key={String(c.key)} className={c.num ? 'num' : ''}>
                {c.term ? <Term label={c.label} term={c.term} /> : c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}
              className={selected === r.symbol ? 'sel' : ''}
              onClick={() => onSelect(r.symbol)}>
              {COLUMNS.map((c) => {
                if (c.key === 'status_label') {
                  return (
                    <td key="status">
                      <span className={statusClass(r.status_label)}>
                        {r.status_label ?? '—'}
                      </span>
                    </td>
                  )
                }
                return (
                  <td key={String(c.key)} className={c.num ? 'num' : ''}>
                    {fmt(r[c.key], c.num)}
                  </td>
                )
              })}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={COLUMNS.length} className="empty">无命中标的</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
