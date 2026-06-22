import type { Filters } from '../types'
import Term from './Term'

interface Props {
  filters: Filters
  boardOptions: string[]
  statusOptions: string[]
  onChange: (f: Filters) => void
  loading: boolean
}

export default function FiltersPanel({
  filters, boardOptions, statusOptions, onChange, loading,
}: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch })

  const toggle = (key: 'boards' | 'statuses', value: string) => {
    const cur = filters[key]
    const next = cur.includes(value)
      ? cur.filter((v) => v !== value)
      : [...cur, value]
    set({ [key]: next } as Partial<Filters>)
  }

  return (
    <aside className="sidebar">
      <h3>筛选条件</h3>

      <label className="field">
        <span><Term label="PE" /> 上限：{filters.pe_max}</span>
        <input type="range" min={0} max={100} value={filters.pe_max}
          onChange={(e) => set({ pe_max: Number(e.target.value) })} />
      </label>

      <label className="field">
        <span><Term label="ROE" /> 下限(%)：{filters.roe_min}</span>
        <input type="range" min={-10} max={40} value={filters.roe_min}
          onChange={(e) => set({ roe_min: Number(e.target.value) })} />
      </label>

      <label className="field">
        <span>取前 N：</span>
        <input type="number" min={5} max={100} value={filters.top}
          onChange={(e) => set({ top: Number(e.target.value) })} />
      </label>

      <div className="field">
        <span><Term label="板块" /></span>
        <div className="checks">
          {boardOptions.map((b) => (
            <label key={b} className="check">
              <input type="checkbox" checked={filters.boards.includes(b)}
                onChange={() => toggle('boards', b)} />
              {b}
            </label>
          ))}
        </div>
      </div>

      <div className="field">
        <span><Term label="状态" /></span>
        <div className="checks">
          {statusOptions.map((s) => (
            <label key={s} className="check">
              <input type="checkbox" checked={filters.statuses.includes(s)}
                onChange={() => toggle('statuses', s)} />
              {s}
            </label>
          ))}
        </div>
      </div>

      {loading && <div className="loading">加载中…</div>}
    </aside>
  )
}
