import { useEffect, useState } from 'react'
import type { Candidate, BacktestResponse, DailyResponse, ReportResponse } from '../types'
import { fetchBacktest, fetchDaily, fetchReport } from '../api'
import PriceChart from './PriceChart'
import Term from './Term'

interface Props {
  symbol: string
  candidate?: Candidate
}

function pct(v: number | undefined): string {
  return v === undefined ? '—' : `${(v * 100).toFixed(2)}%`
}

export default function StockDetail({ symbol, candidate }: Props) {
  const [daily, setDaily] = useState<DailyResponse | null>(null)
  const [bt, setBt] = useState<BacktestResponse | null>(null)
  const [report, setReport] = useState<ReportResponse | null>(null)
  const [reporting, setReporting] = useState(false)

  useEffect(() => {
    let alive = true
    setDaily(null); setBt(null); setReport(null)
    fetchDaily(symbol).then((d) => alive && setDaily(d)).catch(() => {})
    fetchBacktest(symbol).then((b) => alive && setBt(b)).catch(() => {})
    return () => { alive = false }
  }, [symbol])

  const onReport = async () => {
    setReporting(true)
    try {
      setReport(await fetchReport(symbol))
    } finally {
      setReporting(false)
    }
  }

  const status = candidate?.status_label
  const risky = status === 'ST' || status === '退市'

  return (
    <section className="detail">
      <div className="detail-head">
        <h2>{candidate?.name ?? symbol} <span className="code">{symbol}</span></h2>
        {candidate?.board && (
          <span className="chip"><Term label={`板块：${candidate.board}`} term="板块" /></span>
        )}
        {status && (
          <span className={`chip ${risky ? 'chip-risk' : ''}`}>
            <Term label={`状态：${status}`} term="状态" />
          </span>
        )}
      </div>
      {risky && (
        <div className="warn">⚠️ 该标的为「{status}」，风险较高，请谨慎。</div>
      )}

      <div className="detail-grid">
        <div className="panel">
          <h4><Term label="K 线 / 均线" /> （<Term label="后复权" />）</h4>
          {daily ? <PriceChart points={daily.points} />
                 : <div className="muted">加载中…</div>}
        </div>

        <div className="panel">
          <h4>单只回测 <span className="muted">MA 5/20 交叉 · <Term label="交易摩擦" /> · <Term label="T+1" /></span></h4>
          {bt && !bt.error ? (
            <>
              <div className="muted small">引擎：<code>{bt.engine}</code></div>
              <div className="metrics">
                <Metric label="总收益" value={pct(bt.total_return)} />
                <Metric label="年化" value={pct(bt.annual_return)} term="年化收益" />
                <Metric label="夏普" value={String(bt.sharpe ?? '—')} term="夏普比率" />
                <Metric label="最大回撤" value={pct(bt.max_drawdown)} term="最大回撤" />
                <Metric label="交易次数" value={String(bt.trades ?? '—')} />
              </div>
            </>
          ) : <div className="muted">{bt?.error ?? '回测中…'}</div>}
        </div>
      </div>

      <div className="panel">
        <h4><Term label="AI 分析报告" term="RAG" /></h4>
        <button className="btn" onClick={onReport} disabled={reporting}>
          {reporting ? '生成中…' : '生成 AI 报告'}
        </button>
        {report && <div className="report">{report.text}</div>}
      </div>
    </section>
  )
}

function Metric({ label, value, term }: { label: string; value: string; term?: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{term ? <Term label={label} term={term} /> : label}</div>
      <div className="metric-value">{value}</div>
    </div>
  )
}
