// 后端 API 调用封装。开发态经 Vite 代理 /api → Django:8000。
import type {
  Meta, CandidatesResponse, DailyResponse, BacktestResponse,
  ReportResponse, Filters, StrategyBacktestResponse, ResearchReport,
} from './types'

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (!resp.ok) {
    throw new Error(`请求失败 ${resp.status}: ${url}`)
  }
  return resp.json() as Promise<T>
}

export function fetchMeta(): Promise<Meta> {
  return getJSON<Meta>('/api/meta/')
}

export function fetchCandidates(f: Filters): Promise<CandidatesResponse> {
  const p = new URLSearchParams({
    pe_max: String(f.pe_max),
    roe_min: String(f.roe_min),
    top: String(f.top),
  })
  if (f.boards.length) p.set('boards', f.boards.join(','))
  if (f.statuses.length) p.set('statuses', f.statuses.join(','))
  return getJSON<CandidatesResponse>(`/api/candidates/?${p.toString()}`)
}

export function fetchDaily(
  symbol: string, start?: string, end?: string,
): Promise<DailyResponse> {
  const p = new URLSearchParams()
  if (start) p.set('start', start)
  if (end) p.set('end', end)
  const qs = p.toString()
  return getJSON<DailyResponse>(`/api/stocks/${symbol}/daily/${qs ? `?${qs}` : ''}`)
}

export function fetchBacktest(symbol: string): Promise<BacktestResponse> {
  return getJSON<BacktestResponse>(`/api/stocks/${symbol}/backtest/`)
}

export function fetchReport(symbol: string): Promise<ReportResponse> {
  return getJSON<ReportResponse>(`/api/stocks/${symbol}/report/`, { method: 'POST' })
}

export function fetchStrategyBacktest(
  top: number, freq: string,
): Promise<StrategyBacktestResponse> {
  const p = new URLSearchParams({ top: String(top), freq })
  return getJSON<StrategyBacktestResponse>(`/api/strategy/backtest/?${p.toString()}`)
}

export function fetchResearchReport(freq: string, top: number): Promise<ResearchReport> {
  const p = new URLSearchParams({ freq, top: String(top) })
  return getJSON<ResearchReport>(`/api/research/report/?${p.toString()}`)
}
