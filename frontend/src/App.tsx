import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchCandidates, fetchMeta } from './api'
import type { CandidatesResponse, Filters, Meta } from './types'
import { GlossaryContext } from './components/Term'
import StatusBar from './components/StatusBar'
import FiltersPanel from './components/Filters'
import CandidateTable from './components/CandidateTable'
import StockDetail from './components/StockDetail'
import StockQuery from './components/StockQuery'
import StrategyBacktest from './components/StrategyBacktest'

const DEFAULT_FILTERS: Filters = {
  pe_max: 30, roe_min: 10, top: 20, boards: [], statuses: [],
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [data, setData] = useState<CandidatesResponse | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inited = useRef(false)

  // 启动：加载元信息，并把状态默认设为「排除退市」
  useEffect(() => {
    fetchMeta()
      .then((m) => {
        setMeta(m)
        setFilters((f) => ({
          ...f,
          statuses: m.status_labels.filter((s) => s !== '退市'),
        }))
        inited.current = true
      })
      .catch((e) => setError(String(e)))
  }, [])

  // 筛选变化 → 拉候选（防抖 250ms）
  useEffect(() => {
    if (!inited.current) return
    setLoading(true)
    const t = setTimeout(() => {
      fetchCandidates(filters)
        .then((d) => {
          setData(d)
          setError(null)
          setSelected((cur) => {
            if (cur && d.rows.some((r) => r.symbol === cur)) return cur
            return d.rows[0]?.symbol ?? null
          })
        })
        .catch((e) => setError(String(e)))
        .finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(t)
  }, [filters])

  const selectedCandidate = useMemo(
    () => data?.rows.find((r) => r.symbol === selected),
    [data, selected],
  )

  if (error && !meta) {
    return <div className="fatal">无法连接后端：{error}<br />
      请确认 Django 已在 :8000 运行（见 README）。</div>
  }
  if (!meta) return <div className="fatal">加载中…</div>

  return (
    <GlossaryContext.Provider value={meta.glossary}>
      <div className="app">
        <header className="topbar">
          <h1>📈 个人选股 App · A 股</h1>
          <StatusBar meta={meta} />
        </header>

        <div className="body">
          <FiltersPanel
            filters={filters}
            boardOptions={data?.board_options ?? []}
            statusOptions={data?.status_options ?? meta.status_labels}
            onChange={setFilters}
            loading={loading}
          />

          <main className="content">
            <StockQuery initialSymbol={selected ?? undefined} />

            <h3>候选股（命中 {data?.count ?? 0} 只）</h3>
            <p className="muted small">
              分类维度：板块（主板/创业板/科创板/北交所） + 状态（正常/ST/退市）。
              鼠标悬停表头 ⓘ 查看说明；默认已排除退市标的。
            </p>
            <CandidateTable
              rows={data?.rows ?? []}
              selected={selected}
              onSelect={setSelected}
            />

            {selected && (
              <StockDetail symbol={selected} candidate={selectedCandidate} />
            )}

            <StrategyBacktest />
          </main>
        </div>

        <footer className="disclaimer">{meta.disclaimer}</footer>
      </div>
    </GlossaryContext.Provider>
  )
}
