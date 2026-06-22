// 专有名词 + 右上角 ⓘ，悬停显示解释（原生 title tooltip，无第三方依赖）。
import { createContext, useContext } from 'react'

export const GlossaryContext = createContext<Record<string, string>>({})

export function useGlossary() {
  return useContext(GlossaryContext)
}

interface TermProps {
  label: string
  /** 词典键；缺省时用 label 查 */
  term?: string
}

export default function Term({ label, term }: TermProps) {
  const glossary = useGlossary()
  const desc = glossary[term ?? label]
  if (!desc) return <>{label}</>
  return (
    <span className="term">
      {label}
      <sup className="term-info" title={desc}>&#9432;</sup>
    </span>
  )
}
