// 策略回测：股票池级 · walk-forward · 多因子（含摩擦/涨跌停/基准/IC，PIT 防前视）。
import { useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend,
} from 'recharts'
import { fetchStrategyBacktest } from '../api'
import type { StrategyBacktestResponse } from '../types'
import Term from './Term'

const pct = (v: number) => `${(v * 100).toFixed(2)}%`

export default function StrategyBacktest() {
  const [top, setTop] = useState(20)
  const [freq, setFreq] = useState('M')
  const [data, setData] = useState<StrategyBacktestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setLoading(true); setErr(null)
    try {
      setData(await fetchStrategyBacktest(top, freq))
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  const m = data?.metrics
  return (
    <section className="panel">
      <h4>策略回测 <span className="muted small">
        股票池级 · walk-forward · 含 <Term label="交易摩擦" /> / 涨跌停 / 基准 / <Term label="多因子综合得分" term="多因子综合得分" />（PIT 防前视）
      </span></h4>

      <div className="query-form">
        <label>持仓 top<input type="number" min={1} max={100} value={top}
          onChange={(e) => setTop(Number(e.target.value))} style={{ width: 70 }} /></label>
        <label>调仓
          <select value={freq} onChange={(e) => setFreq(e.target.value)}>
            <option value="M">每月</option>
            <option value="W">每周</option>
            <option value="Q">每季</option>
          </select>
        </label>
        <button className="btn" onClick={run} disabled={loading}>
          {loading ? '回测中…' : '运行回测'}
        </button>
        {err && <span className="muted small err">{err}</span>}
      </div>

      {m && (
        <>
          <div className="metrics">
            <Metric label="总收益" value={pct(m.total_return)} />
            <Metric label="年化" value={pct(m.annual_return)} term="年化收益" />
            <Metric label="基准收益" value={pct(m.benchmark_return)} />
            <Metric label="超额" value={pct(m.excess_return)}
              good={m.excess_return >= 0} />
            <Metric label="夏普" value={String(m.sharpe)} term="夏普比率" />
            <Metric label="最大回撤" value={pct(m.max_drawdown)} term="最大回撤" />
            <Metric label="IC 均值" value={String(m.ic_mean)} term="多因子综合得分" />
            <Metric label="ICIR" value={String(m.ic_ir)} />
            <Metric label="盈亏比" value={String(m.profit_loss_ratio)} />
            <Metric label="期望值/期" value={m.expectancy.toFixed(4)} />
            <Metric label="调仓次数" value={String(m.n_rebalances)} />
            <Metric label="平均持仓" value={`${m.avg_positions} 只`} />
          </div>

          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={data!.curve} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={48} />
              <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
              <Tooltip formatter={(v: number) => (typeof v === 'number' ? v.toFixed(3) : v)} />
              <Legend />
              <Line type="monotone" dataKey="equity" name="策略净值" stroke="#2e9b8f"
                dot={false} strokeWidth={1.6} isAnimationActive={false} />
              <Line type="monotone" dataKey="benchmark" name="基准" stroke="#9aa3b2"
                dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
          <p className="muted small">
            注：合成/无真实 alpha 数据下 IC 应≈0、可能跑输基准——这是"不造假收益"的正常表现。
          </p>
        </>
      )}
    </section>
  )
}

function Metric({ label, value, term, good }:
  { label: string; value: string; term?: string; good?: boolean }) {
  return (
    <div className="metric">
      <div className="metric-label">{term ? <Term label={label} term={term} /> : label}</div>
      <div className="metric-value" style={good === false ? { color: '#d9534f' }
        : good === true ? { color: '#2e9b8f' } : undefined}>{value}</div>
    </div>
  )
}
