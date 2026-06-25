// 每日推荐 + 战绩跟踪（阶段三）：最新一期推荐 + 历史推荐的真实前向收益。
import { useEffect, useState } from 'react'
import { fetchRecommendations, fetchRecommendationPerformance } from '../api'
import type { RecommendationsResponse, RecommendationPerformance } from '../types'
import Term from './Term'

const pct = (v: number | null) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(2)}%`

function retColor(v: number | null) {
  if (v === null || v === undefined) return undefined
  return v > 0 ? '#d9534f' : v < 0 ? '#2e9b8f' : undefined   // A股 红涨绿跌
}

export default function RecommendationsPanel() {
  const [data, setData] = useState<RecommendationsResponse | null>(null)
  const [perf, setPerf] = useState<RecommendationPerformance | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetchRecommendations().then(setData).catch((e) => setErr(String(e)))
    fetchRecommendationPerformance().then(setPerf).catch(() => {})
  }, [])

  if (err) return null
  return (
    <section className="panel">
      <h4>每日推荐 · 战绩跟踪 <span className="muted small">
        每个交易日 sync 自动落库 top-N；<Term label="前向收益" term="年化收益" />事后回填，用真实表现证明高回报
      </span></h4>

      {perf && (
        <div className="metrics" style={{ marginBottom: 8 }}>
          <Metric label="累计推荐" value={`${perf.n_total} 条`} />
          <Metric label="推荐天数" value={`${perf.n_dates} 天`} />
          <Metric label="5日平均收益" value={pct(perf.avg_5d)} color={retColor(perf.avg_5d)} />
          <Metric label="5日胜率" value={perf.win_5d === null ? '—' : `${(perf.win_5d * 100).toFixed(0)}%`} />
          <Metric label="20日平均收益" value={pct(perf.avg_20d)} color={retColor(perf.avg_20d)} />
          <Metric label="20日胜率" value={perf.win_20d === null ? '—' : `${(perf.win_20d * 100).toFixed(0)}%`} />
        </div>
      )}

      {data && data.latest_date ? (
        <>
          <div className="muted small">最新一期：{data.latest_date}（前向收益为空=未来行情尚未到位，sync 会自动回填）</div>
          <div className="table-wrap" style={{ marginTop: 6 }}>
            <table className="candidates">
              <thead><tr>
                <th>排名</th><th>代码</th><th>名称</th><th>板块</th>
                <th className="num">综合得分</th><th className="num">PE</th><th className="num">ROE</th>
                <th className="num">5日收益</th><th className="num">20日收益</th>
              </tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.symbol}>
                    <td>{r.rank}</td><td>{r.symbol}</td><td>{r.name ?? '—'}</td>
                    <td>{r.board ?? '—'}</td>
                    <td className="num">{r.score?.toFixed(4) ?? '—'}</td>
                    <td className="num">{r.pe?.toFixed(2) ?? '—'}</td>
                    <td className="num">{r.roe?.toFixed(2) ?? '—'}</td>
                    <td className="num" style={{ color: retColor(r.fwd_5d) }}>{pct(r.fwd_5d)}</td>
                    <td className="num" style={{ color: retColor(r.fwd_20d) }}>{pct(r.fwd_20d)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="muted small">暂无推荐。运行 <code>aselect.cli sync</code> 后每个交易日自动生成。</div>
      )}
    </section>
  )
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value" style={color ? { color } : undefined}>{value}</div>
    </div>
  )
}
