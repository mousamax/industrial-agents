// Light markdown without a dependency: paragraphs, line breaks, bullet and numbered lists, headings as bold,
// **bold**, *italic*, `code`. Builds React elements (no innerHTML).
import type { ReactNode } from 'react'

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*)/g
  let last = 0
  let i = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyBase}-${i++}`
    if (tok.startsWith('**') || tok.startsWith('__')) out.push(<strong key={key} className="font-semibold">{tok.slice(2, -2)}</strong>)
    else if (tok.startsWith('`')) out.push(<code key={key} className="rounded bg-map-bg px-1 text-[0.9em]">{tok.slice(1, -1)}</code>)
    else out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

type Block =
  | { kind: 'p'; lines: string[] }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[]; start: number }
  | { kind: 'h'; text: string }

function blocks(text: string): Block[] {
  const out: Block[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const last = out[out.length - 1]
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/)
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/)
    if (!line.trim()) out.push({ kind: 'p', lines: [] })
    else if (heading) out.push({ kind: 'h', text: heading[1] })
    else if (bullet) {
      if (last?.kind === 'ul') last.items.push(bullet[1])
      else out.push({ kind: 'ul', items: [bullet[1]] })
    } else if (numbered) {
      if (last?.kind === 'ol') last.items.push(numbered[2])
      else out.push({ kind: 'ol', items: [numbered[2]], start: Number(numbered[1]) })
    } else if (/^\s{2,}\S/.test(raw) && (last?.kind === 'ul' || last?.kind === 'ol')) {
      last.items[last.items.length - 1] += ' ' + line.trim() // continuation of a list item
    } else if (last?.kind === 'p' && last.lines.length) last.lines.push(line)
    else out.push({ kind: 'p', lines: [line] })
  }
  return out.filter((b) => b.kind !== 'p' || b.lines.length)
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-[15px] leading-relaxed text-ink">
      {blocks(text).map((b, bi) => {
        const k = `b${bi}`
        switch (b.kind) {
          case 'h':
            return <p key={k} className="font-semibold">{inline(b.text, k)}</p>
          case 'ul':
            return (
              <ul key={k} className="list-disc space-y-1 pl-5 marker:text-ink-soft">
                {b.items.map((it, ii) => <li key={ii}>{inline(it, `${k}-${ii}`)}</li>)}
              </ul>
            )
          case 'ol':
            return (
              <ol key={k} start={b.start} className="list-decimal space-y-1 pl-5 marker:text-ink-soft">
                {b.items.map((it, ii) => <li key={ii}>{inline(it, `${k}-${ii}`)}</li>)}
              </ol>
            )
          default:
            return (
              <p key={k}>
                {b.lines.map((l, li) => (
                  <span key={li}>
                    {li > 0 && <br />}
                    {inline(l, `${k}-${li}`)}
                  </span>
                ))}
              </p>
            )
        }
      })}
    </div>
  )
}
