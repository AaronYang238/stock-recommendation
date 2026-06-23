// 个股查询：输入代码 + 起止日期 → 查看指定阶段内的 K 线（任意股票，不限候选池）。
import { useState } from 'react'
import { fetchDaily } from '../api'
import type { DailyResponse } from '../types'
import PriceChart from './PriceChart'
import Term from './Term'

export default function StockQuery({ initialSymbol }: { initialSymbol?: string }) {
  const [code, setCode] = useState(initialSymbol ?? '')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [data, setData] = useState<DailyResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const query = async () => {
    const sym = code.trim()
    if (!sym) { setErr('请输入股票代码，如 600519'); return }
    setLoading(true); setErr(null)
    try {
      const d = await fetchDaily(sym, start || undefined, end || undefined)
      setData(d)
      if (!d.points.length) {
        setErr(d.available
          ? '该区间无数据，请调整起止日期。'
          : `本地库无 ${sym} 的行情，请先 update/seed 或更换代码。`)
      }
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  const onKey = (e: React.KeyboardEvent) => { if (e.key === 'Enter') query() }

  return (
    <section className="panel query">
      <h4>个股查询 · <Term label="K 线" term="MA" />（<Term label="后复权" />，指定区间）</h4>
      <div className="query-form">
        <input className="q-code" placeholder="股票代码 如 600519"
          value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={onKey} />
        <label>起 <input type="date" value={start}
          onChange={(e) => setStart(e.target.value)} /></label>
        <label>止 <input type="date" value={end}
          onChange={(e) => setEnd(e.target.value)} /></label>
        <button className="btn" onClick={query} disabled={loading}>
          {loading ? '查询中…' : '查询'}
        </button>
        {data?.available && (
          <span className="muted small">
            可查范围 {data.available.start} ~ {data.available.end}
          </span>
        )}
      </div>

      {err && <div className="muted small err">{err}</div>}
      {data && data.points.length > 0 && (
        <>
          <div className="muted small">
            {data.name ?? data.symbol}（{data.symbol}） · 共 {data.points.length} 个交易日
          </div>
          <PriceChart points={data.points} />
        </>
      )}
    </section>
  )
}
