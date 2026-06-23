// K 线（蜡烛图）+ MA20/MA60。A 股惯例：收 ≥ 开为红，否则为绿。
// 用 recharts 的「区间 Bar(low→high) + 自定义 shape」绘制蜡烛，无需第三方图库。
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  ResponsiveContainer, Legend,
} from 'recharts'
import type { DailyPoint } from '../types'

const UP = '#d9534f'     // 红涨
const DOWN = '#2e9b8f'   // 绿跌

interface Row extends DailyPoint {
  hl: [number, number]   // [low, high]，给区间 Bar
}

function Candle(props: any) {
  const { x, y, width, height, payload } = props
  const { open, close, high, low } = payload as DailyPoint
  if (open == null || close == null || high == null || low == null) return null
  const color = close >= open ? UP : DOWN
  const span = high - low
  const ratio = span === 0 ? 0 : height / span      // 像素/价
  const yOpen = y + (high - open) * ratio
  const yClose = y + (high - close) * ratio
  const bodyY = Math.min(yOpen, yClose)
  const bodyH = Math.max(1, Math.abs(yClose - yOpen))
  const cx = x + width / 2
  const bw = Math.max(1, width * 0.6)
  return (
    <g stroke={color} fill={color}>
      <line x1={cx} x2={cx} y1={y} y2={y + height} strokeWidth={1} />
      <rect x={cx - bw / 2} y={bodyY} width={bw} height={bodyH} />
    </g>
  )
}

export default function PriceChart({ points }: { points: DailyPoint[] }) {
  if (!points.length) return <div className="muted">无行情数据</div>
  const data: Row[] = points.map((p) => ({
    ...p,
    hl: [p.low ?? p.close ?? 0, p.high ?? p.close ?? 0],
  }))
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={48} />
        <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} allowDecimals />
        <Tooltip
          formatter={(v: number | string, name: string) =>
            [typeof v === 'number' ? v.toFixed(2) : v, name]}
          labelFormatter={(l) => `日期 ${l}`}
        />
        <Legend />
        <Bar dataKey="hl" name="K线" shape={<Candle />} isAnimationActive={false}
          legendType="none" />
        <Line type="monotone" dataKey="ma20" name="MA20" stroke="#e8a33d"
          dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />
        <Line type="monotone" dataKey="ma60" name="MA60" stroke="#7b8cde"
          dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
