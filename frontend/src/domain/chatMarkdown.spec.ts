import { describe, expect, it } from 'vitest'

import { parseMarkdown } from './chatMarkdown'

function text(value: string) {
  return { type: 'text', value }
}

describe('chat markdown parser', () => {
  it('parses a plain line as a single paragraph', () => {
    expect(parseMarkdown('毕业总学分为 155.0')).toEqual([
      { type: 'paragraph', lines: [[text('毕业总学分为 155.0')]] },
    ])
  })

  it('splits paragraphs on blank lines and keeps single newlines inside one paragraph', () => {
    expect(parseMarkdown('第一段\n\n第二段\n第三行')).toEqual([
      { type: 'paragraph', lines: [[text('第一段')]] },
      { type: 'paragraph', lines: [[text('第二段')], [text('第三行')]] },
    ])
  })

  it('normalizes CRLF input', () => {
    expect(parseMarkdown('第一段\r\n\r\n第二段')).toEqual([
      { type: 'paragraph', lines: [[text('第一段')]] },
      { type: 'paragraph', lines: [[text('第二段')]] },
    ])
  })

  it('parses unordered and ordered lists', () => {
    expect(parseMarkdown('- 第一条\n- 第二条')).toEqual([
      { type: 'list', ordered: false, items: [[text('第一条')], [text('第二条')]] },
    ])
    expect(parseMarkdown('1. 第一条\n2. 第二条')).toEqual([
      { type: 'list', ordered: true, items: [[text('第一条')], [text('第二条')]] },
    ])
  })

  it('parses fenced code blocks verbatim', () => {
    expect(parseMarkdown('```\nconst a = 1\n<b>粗体</b>\n```')).toEqual([
      { type: 'code', value: 'const a = 1\n<b>粗体</b>' },
    ])
  })

  it('parses inline code, strong and emphasis', () => {
    expect(parseMarkdown('这是 `代码` 与 **重点** 与 *斜体*')).toEqual([
      {
        type: 'paragraph',
        lines: [
          [
            text('这是 '),
            { type: 'code', value: '代码' },
            text(' 与 '),
            { type: 'strong', value: '重点' },
            text(' 与 '),
            { type: 'em', value: '斜体' },
          ],
        ],
      },
    ])
  })

  it('parses citation markers as citation nodes', () => {
    expect(parseMarkdown('依据 [1] 与 [12]')).toEqual([
      {
        type: 'paragraph',
        lines: [[text('依据 '), { type: 'citation', index: 1 }, text(' 与 '), { type: 'citation', index: 12 }]],
      },
    ])
  })

  it('does not treat non-numeric brackets as citations', () => {
    expect(parseMarkdown('见 [附录] 与 [abc]')).toEqual([
      { type: 'paragraph', lines: [[text('见 [附录] 与 [abc]')]] },
    ])
  })

  it('keeps a citation marker inside a code span as code', () => {
    expect(parseMarkdown('写作 `[1]` 表示引用')).toEqual([
      {
        type: 'paragraph',
        lines: [[text('写作 '), { type: 'code', value: '[1]' }, text(' 表示引用')]],
      },
    ])
  })

  it('keeps raw HTML as plain text instead of parsing it', () => {
    const blocks = parseMarkdown('<script>alert(1)</script>\n<a href="https://x">链接</a>')

    expect(blocks).toEqual([
      {
        type: 'paragraph',
        lines: [[text('<script>alert(1)</script>')], [text('<a href="https://x">链接</a>')]],
      },
    ])
    expect(JSON.stringify(blocks)).not.toContain('"type":"html"')
  })

  it('parses block quotes and ignores leading blank lines', () => {
    expect(parseMarkdown('\n> 引用内容\n\n正文')).toEqual([
      { type: 'quote', lines: [[text('引用内容')]] },
      { type: 'paragraph', lines: [[text('正文')]] },
    ])
  })

  it('returns no blocks for empty input', () => {
    expect(parseMarkdown('')).toEqual([])
    expect(parseMarkdown('   \n\n  ')).toEqual([])
  })
})
