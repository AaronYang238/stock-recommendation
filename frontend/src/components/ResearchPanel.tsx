// 因子/策略研究（阶段二）：单因子 walk-forward IC 表 + 样本外(hold-out)验证。
import { useState } from 'react'
import { fetchResearchReport } from '../api'
import type { ResearchReport, ValidatedMetrics } from '../types'
import Term from './Term'

const pct = (v: number) => `${(v * 100).toFixed(2)}%`

export default function ResearchPanel() {
  const [freq, setFreq] = useState('M')
  const [data, setData] = useState<ResearchReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setLoading(true); setErr(null)
    try {
      setData(await fetchResearchReport(freq, 20))
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  const v = data?.validated
  return (
    <section className="panel">
      <h4>因子/策略研究 <span className="muted small">
        单因子 <Term label="walk-forward IC" term="多因子综合得分" /> + 样本外(hold-out)验证（北极星：样本外真实收益）
      </span></h4>

      <div className="query-form">
        <label>调仓
          <select value={freq} onChange={(e) => setFreq(e.target.value)}>
            <option value="M">每月</option>
            <option value="W">每周</option>
            <option value="Q">每季</option>
          </select>
        </label>
        <button className="btn" onClick={run} disabled={loading}>
          {loading ? '研究中…（构建逐期 PIT 截面，稍慢）' : '运行因子研究'}
        </button>
        {err && <span className="muted small err">{err}</span>}
      </div>

      {data && (
        <>
          <div className="muted small">单因子 IC（|IC|稳定 0.03~0.05 即好因子，ICIR&gt;0.5 佳；随机/无 alpha 数据 IC≈0）</div>
          <div className="table-wrap" style={{ marginTop: 6 }}>
            <table className="candidates">
              <thead><tr>
                <th>因子</th><th className="num">IC 均值</th><th className="num">ICIR</th>
                <th className="num">IC 胜率</th><th className="num">分层多空</th><th className="num">样本</th>
              </tr></thead>
              <tbody>
                {data.factors.map((f) => (
                  <tr key={f.name}>
                    <td>{f.name}</td>
                    <td className="num" style={{ color: f.ic_mean > 0.02 ? '#2e9b8f' : f.ic_mean < -0.02 ? '#d9534f' : undefined }}>{f.ic_mean.toFixed(4)}</td>
                    <td className="num">{f.icir}</td>
                    <td className="num">{(f.ic_win_rate * 100).toFixed(0)}%</td>
                    <td className="num">{f.quantile_spread.toFixed(4)}</td>
                    <td className="num">{f.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {v ? (
            <div style={{ marginTop: 12 }}>
              <div className="muted small">
                样本外纪律：切分日 <b>{v.split_date}</b> 前为训练段（拟合 IC 权重：
                {Object.entries(v.weights).map(([k, w]) => `${k} ${w.toFixed(2)}`).join(' / ')}），
                之后为<b>只测一次</b>的样本外段。
              </div>
              <div className="oos-cols">
                <ValidatedCard title="训练段(样本内)" m={v.train} />
                <ValidatedCard title="样本外(只测一次)" m={v.oos} highlight />
              </div>
            </div>
          ) : <div className="muted small">{data.note ?? '样本不足，无法切分样本外。'}</div>}
        </>
      )}
    </section>
  )
}

function ValidatedCard({ title, m, highlight }:
  { title: string; m: ValidatedMetrics; highlight?: boolean }) {
  return (
    <div className="panel" style={highlight ? { borderColor: '#2e9b8f', marginTop: 8 } : { marginTop: 8 }}>
      <div className="metric-label">{title}</div>
      <div className="metrics">
        <Metric label="总收益" value={pct(m.total_return)} />
        <Metric label="年化" value={pct(m.annual_return)} term="年化收益" />
        <Metric label="夏普" value={String(m.sharpe)} term="夏普比率" />
        <Metric label="最大回撤" value={pct(m.max_drawdown)} term="最大回撤" />
        <Metric label="超额" value={pct(m.excess_return)} />
        <Metric label="IC" value={String(m.ic_mean)} />
        <Metric label="盈亏比" value={String(m.profit_loss_ratio)} />
      </div>
    </div>
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
