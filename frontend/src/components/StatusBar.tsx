import type { Meta } from '../types'

export default function StatusBar({ meta }: { meta: Meta }) {
  const aiText = meta.ai.enabled ? `启用 · ${meta.ai.provider}` : '禁用(降级)'
  const cards = [
    { label: '股票池', value: `${meta.n_symbols} 只`,
      hint: '含历史退市/ST 标的，避免幸存者偏差' },
    { label: '指标后端', value: meta.indicator_backend,
      hint: 'pandas-ta 不可用时回退到经测试的向量化实现' },
    { label: 'AI', value: aiText,
      hint: 'AI 为热插拔模块；禁用或缺 Key 时降级，确定性核心照常运行' },
  ]
  return (
    <div className="statusbar">
      {cards.map((c) => (
        <div className="status-card" key={c.label} title={c.hint}>
          <div className="status-label">{c.label}</div>
          <div className="status-value">{c.value}</div>
        </div>
      ))}
    </div>
  )
}
