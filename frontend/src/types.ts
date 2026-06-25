// 与后端 /api 返回结构对应的类型定义。

export interface Meta {
  n_symbols: number
  indicator_backend: string
  ai: { enabled: boolean; provider: string; model: string }
  data?: { last_daily_date: string | null; n_with_fundamentals: number }
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
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  ma20: number | null
  ma60: number | null
}

export interface DailyResponse {
  symbol: string
  name?: string | null
  adjust?: string
  start?: string | null
  end?: string | null
  available?: { start: string; end: string } | null
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

export interface StrategyMetrics {
  total_return: number
  annual_return: number
  sharpe: number
  max_drawdown: number
  benchmark_return: number
  excess_return: number
  ic_mean: number
  ic_ir: number
  ic_win_rate: number
  win_rate: number
  profit_loss_ratio: number
  expectancy: number
  n_rebalances: number
  avg_turnover: number
  avg_positions: number
}

export interface Recommendation {
  date: string
  symbol: string
  name: string | null
  rank: number | null
  score: number | null
  board: string | null
  status: string | null
  pe: number | null
  roe: number | null
  fwd_5d: number | null
  fwd_20d: number | null
}

export interface RecommendationsResponse {
  rows: Recommendation[]
  dates: string[]
  latest_date: string | null
}

export interface RecommendationPerformance {
  n_total: number
  n_dates: number
  avg_5d: number | null
  win_5d: number | null
  n_5d: number
  avg_20d: number | null
  win_20d: number | null
  n_20d: number
}

export interface FactorIC {
  name: string
  ic_mean: number
  icir: number
  ic_win_rate: number
  quantile_spread: number
  n: number
  decay: number[]
}

export interface ValidatedMetrics {
  total_return: number
  annual_return: number
  sharpe: number
  max_drawdown: number
  excess_return: number
  ic_mean: number
  profit_loss_ratio: number
  n_rebalances: number
}

export interface ResearchReport {
  freq: string
  factors: FactorIC[]
  validated: {
    split_date: string
    weights: Record<string, number>
    train: ValidatedMetrics
    oos: ValidatedMetrics
  } | null
  note?: string | null
}

export interface StrategyCurvePoint {
  date: string
  equity: number
  benchmark: number | null
}

export interface StrategyBacktestResponse {
  params: { top: number; freq: string; start: string | null; end: string | null }
  metrics: StrategyMetrics
  curve: StrategyCurvePoint[]
}

export interface Filters {
  pe_max: number
  roe_min: number
  top: number
  boards: string[]
  statuses: string[]
}
