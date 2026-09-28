/**
 * 回答正文的安全结构化解析（UI_SPEC 6.5 / 9）。
 *
 * 只把**纯文本**解析成一小撮结构节点，再由 Vue 组件用文本节点渲染。
 * 这里不存在任何 HTML 解析、链接生成或事件属性处理：模型返回的
 * ``<script>``、``<a href>``、``onerror=`` 等内容一律保持为普通文字。
 *
 * 支持：段落、段内换行、有序/无序列表、引用块、代码块、行内代码、
 * 加粗、斜体，以及 ``[1]`` 这类引用编号标记（仅标记，不产生来源）。
 */

export interface TextNode {
  type: 'text'
  value: string
}

export interface CodeNode {
  type: 'code'
  value: string
}

export interface StrongNode {
  type: 'strong'
  value: string
}

export interface EmphasisNode {
  type: 'em'
  value: string
}

/** 回答正文里的 ``[1]``：只是一个标记，是否可用由真实 citation 事件决定。 */
export interface CitationNode {
  type: 'citation'
  index: number
}

export type InlineNode =
  | TextNode
  | CodeNode
  | StrongNode
  | EmphasisNode
  | CitationNode

export interface ParagraphBlock {
  type: 'paragraph'
  lines: InlineNode[][]
}

export interface ListBlock {
  type: 'list'
  ordered: boolean
  items: InlineNode[][]
}

export interface CodeBlock {
  type: 'code'
  value: string
}

export interface QuoteBlock {
  type: 'quote'
  lines: InlineNode[][]
}

export type BlockNode = ParagraphBlock | ListBlock | CodeBlock | QuoteBlock

const INLINE_PATTERN = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[\d+\])/g
const FENCE = /^\s*```/
const UNORDERED_ITEM = /^\s*[-*+]\s+/
const ORDERED_ITEM = /^\s*\d+\.\s+/
const QUOTE_LINE = /^\s*>\s?/

function classifyInline(token: string): InlineNode {
  if (token.startsWith('`')) {
    return { type: 'code', value: token.slice(1, -1) }
  }
  if (token.startsWith('**')) {
    return { type: 'strong', value: token.slice(2, -2) }
  }
  if (token.startsWith('*')) {
    return { type: 'em', value: token.slice(1, -1) }
  }
  return { type: 'citation', index: Number.parseInt(token.slice(1, -1), 10) }
}

/** 解析一行内的行内元素；非数字方括号（如 ``[附录]``）保持为文本。 */
export function parseInline(line: string): InlineNode[] {
  if (line === '') {
    return []
  }
  const nodes: InlineNode[] = []
  let lastIndex = 0
  for (const match of line.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0
    if (index > lastIndex) {
      nodes.push({ type: 'text', value: line.slice(lastIndex, index) })
    }
    nodes.push(classifyInline(match[0]))
    lastIndex = index + match[0].length
  }
  if (lastIndex < line.length) {
    nodes.push({ type: 'text', value: line.slice(lastIndex) })
  }
  return nodes
}

/** 把回答正文解析为块级结构；不做任何 HTML 解释。 */
export function parseMarkdown(source: string): BlockNode[] {
  const lines = (source ?? '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: BlockNode[] = []
  let paragraph: string[] = []

  const flushParagraph = (): void => {
    if (paragraph.length === 0) {
      return
    }
    blocks.push({ type: 'paragraph', lines: paragraph.map(parseInline) })
    paragraph = []
  }

  let index = 0
  while (index < lines.length) {
    const line = lines[index]

    if (FENCE.test(line)) {
      flushParagraph()
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !FENCE.test(lines[index])) {
        codeLines.push(lines[index])
        index += 1
      }
      index += 1
      blocks.push({ type: 'code', value: codeLines.join('\n') })
      continue
    }

    if (UNORDERED_ITEM.test(line) || ORDERED_ITEM.test(line)) {
      flushParagraph()
      const ordered = ORDERED_ITEM.test(line)
      const pattern = ordered ? ORDERED_ITEM : UNORDERED_ITEM
      const items: InlineNode[][] = []
      while (index < lines.length && pattern.test(lines[index])) {
        items.push(parseInline(lines[index].replace(pattern, '')))
        index += 1
      }
      blocks.push({ type: 'list', ordered, items })
      continue
    }

    if (QUOTE_LINE.test(line)) {
      flushParagraph()
      const quoteLines: InlineNode[][] = []
      while (index < lines.length && QUOTE_LINE.test(lines[index])) {
        quoteLines.push(parseInline(lines[index].replace(QUOTE_LINE, '')))
        index += 1
      }
      blocks.push({ type: 'quote', lines: quoteLines })
      continue
    }

    if (line.trim() === '') {
      flushParagraph()
      index += 1
      continue
    }

    paragraph.push(line)
    index += 1
  }

  flushParagraph()
  return blocks
}
