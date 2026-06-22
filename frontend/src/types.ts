// 与后端 /api 返回结构对应的类型定义。

export interface Meta {
  n_symbols: number
  indicator_backend: string
  ai: { enabled: boolean; provider: string; model: string }
  disclaimer: string
  glossary: Record<string, string>
  field_schema: Record<string, string>
  status_labels: string[]
  columns: string[]
}

export interface Candidate {
  symbol: string
  name: string | null
  board: string | null
  status_label: string | null
  pe: number | null
  pb: number | null
  roe: number | null
  revenue_yoy: number | null
  mom_60: number | null
  score_value: number | null
  score_quality: number | null
  total_score: number | null
  [key: string]: string | number | null
}

export interface CandidatesResponse {
  rows: Candidate[]
  count: number
  board_options: string[]
  status_options: string[]
}

export interface DailyPoint {
  date: string
  close: number | null
  ma20: number | null
  ma60: number | null
}

export interface DailyResponse {
  symbol: string
  adjust?: string
  points: DailyPoint[]
}

export interface BacktestResponse {
  symbol: string
  engine?: string
  total_return?: number
  annual_return?: number
  sharpe?: number
  max_drawdown?: number
  trades?: number
  error?: string
}

export interface ReportResponse {
  symbol: string
  text: string
  grounded: boolean
  ai_enabled: boolean
}

export interface Filters {
  pe_max: number
  roe_min: number
  top: number
  boards: string[]
  statuses: string[]
}
