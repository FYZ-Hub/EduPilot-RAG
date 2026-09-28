import { afterEach, describe, expect, it, vi } from 'vitest'

import { API_BASE_URL } from './client'
import {
  calculateAcademicPlan,
  fetchAcademicOptions,
  importAcademicRecords,
  importAcademicRules,
  RECORD_IMPORT_EXTENSIONS,
  RULE_IMPORT_EXTENSIONS,
} from './academic'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function stubFetch(response: Response): ReturnType<typeof vi.fn> {
  const mock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', mock)
  return mock
}

function recordFile(): File {
  return new File(['xlsx-bytes'], '课程记录.xlsx')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('academic options API', () => {
  it('reads options from the dedicated academic endpoint', async () => {
    const payload = {
      record_sets: [
        {
          id: 'rs-1',
          name: '课程记录 A',
          source_doc_id: 'doc-1',
          status: 'ready',
          updated_at: '2026-09-01T00:00:00Z',
        },
      ],
      rule_sets: [
        {
          id: 'ru-1',
          name: '培养方案 2026',
          major: '计算机科学与技术',
          admission_year: 2026,
          rule_version: '2026.1',
          effective_from: '2026-09-01',
          source_doc_id: 'doc-2',
          status: 'ready',
        },
      ],
    }
    const mock = stubFetch(jsonResponse(payload))

    const options = await fetchAcademicOptions()

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/academic/options`)
    expect(init.method ?? 'GET').toBe('GET')
    expect(options).toEqual(payload)
  })

  it('passes an empty options payload through without inventing entries', async () => {
    stubFetch(jsonResponse({ record_sets: [], rule_sets: [] }))

    const options = await fetchAcademicOptions()

    expect(options.record_sets).toEqual([])
    expect(options.rule_sets).toEqual([])
  })
})

describe('academic import API', () => {
  it('posts course records as multipart to the academic endpoint without a name', async () => {
    const mock = stubFetch(jsonResponse({ id: 'rs-1', status: 'ready', warnings: [] }))
    const file = recordFile()

    const result = await importAcademicRecords(file)

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/academic/records/import`)
    expect(init.method).toBe('POST')
    const body = init.body as FormData
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('file')).toBeInstanceOf(File)
    expect((body.get('file') as File).name).toBe(file.name)
    expect(body.get('name')).toBeNull()
    expect(init.headers['Content-Type']).toBeUndefined()
    expect(result).toEqual({ id: 'rs-1', status: 'ready', warnings: [] })
  })

  it('never sends a blank name', async () => {
    const mock = stubFetch(jsonResponse({ id: 'rs-1', status: 'ready', warnings: [] }))

    await importAcademicRecords(recordFile(), '   ')

    expect((mock.mock.calls[0][1].body as FormData).get('name')).toBeNull()
  })

  it('sends a trimmed name when one is provided', async () => {
    const mock = stubFetch(jsonResponse({ id: 'rs-1', status: 'ready', warnings: [] }))

    await importAcademicRecords(recordFile(), '  2026 秋季记录  ')

    expect((mock.mock.calls[0][1].body as FormData).get('name')).toBe('2026 秋季记录')
  })

  it('posts degree rules to the rules import endpoint', async () => {
    const mock = stubFetch(jsonResponse({ id: 'ru-1', status: 'ready', warnings: ['ok'] }))
    const file = new File(['pdf-bytes'], '培养方案.pdf')

    const result = await importAcademicRules(file, '培养方案 2026')

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/academic/rules/import`)
    expect(init.method).toBe('POST')
    const body = init.body as FormData
    expect(body.get('file')).toBeInstanceOf(File)
    expect((body.get('file') as File).name).toBe(file.name)
    expect(body.get('name')).toBe('培养方案 2026')
    expect(result.warnings).toEqual(['ok'])
  })

  it('declares the client-side extension allow lists', () => {
    expect([...RECORD_IMPORT_EXTENSIONS]).toEqual(['xlsx'])
    expect([...RULE_IMPORT_EXTENSIONS]).toEqual(['pdf', 'docx', 'xlsx'])
  })
})

describe('academic plan API', () => {
  it('posts exactly the two selection ids', async () => {
    const mock = stubFetch(
      jsonResponse({
        required_credits: 155.0,
        completed_credits: 0.0,
        in_progress_credits: 0.0,
        remaining_credits: 155.0,
        missing_required_courses: [],
        category_gaps: [],
        conflict_warnings: [],
        evidence: [],
      }),
    )

    await calculateAcademicPlan({ record_set_id: 'rs-1', rule_set_id: 'ru-1' })

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/academic/plan`)
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    const parsed = JSON.parse(init.body as string)
    expect(Object.keys(parsed).sort()).toEqual(['record_set_id', 'rule_set_id'])
    expect(parsed).toEqual({ record_set_id: 'rs-1', rule_set_id: 'ru-1' })
  })

  it('ignores any extra keys a caller tries to attach', async () => {
    const mock = stubFetch(
      jsonResponse({
        required_credits: 0,
        completed_credits: 0,
        in_progress_credits: 0,
        remaining_credits: 0,
        missing_required_courses: [],
        category_gaps: [],
        conflict_warnings: [],
        evidence: [],
      }),
    )

    await calculateAcademicPlan({
      record_set_id: 'rs-1',
      rule_set_id: 'ru-1',
      // @ts-expect-error 后端禁止额外字段，客户端也不得发送
      name: '注入字段',
      major: '注入专业',
    })

    const parsed = JSON.parse(mock.mock.calls[0][1].body as string)
    expect(Object.keys(parsed).sort()).toEqual(['record_set_id', 'rule_set_id'])
  })

  it('passes the planning result through untouched', async () => {
    const payload = {
      required_credits: 155.0,
      completed_credits: 100.5,
      in_progress_credits: 12.0,
      remaining_credits: 42.5,
      missing_required_courses: [
        {
          course_code: 'QM-CS401',
          course_name: '毕业设计',
          credits: 8.0,
          category: '专业必修',
          evidence_chunk_ids: ['c'.repeat(64)],
        },
      ],
      category_gaps: [
        {
          category: '专业必修',
          required_credits: 60.0,
          completed_credits: 40.0,
          in_progress_credits: 12.0,
          remaining_credits: 8.0,
        },
      ],
      conflict_warnings: [
        {
          code: 'COURSE_TIME_CONFLICT',
          message: '两门课程时间冲突',
          severity: 'warning',
          evidence_chunk_ids: ['c'.repeat(64)],
        },
      ],
      evidence: [
        {
          chunk_id: 'c'.repeat(64),
          doc_id: 'doc-1',
          file_name: '培养方案.pdf',
          document_version: '2026.1',
          effective_from: '2026-09-01',
          page_number: 4,
          sheet_name: null,
          row_start: null,
          row_end: null,
          section_title: '培养方案/总则',
          quote: '毕业总学分 155.0',
        },
      ],
    }
    stubFetch(jsonResponse(payload))

    const result = await calculateAcademicPlan({ record_set_id: 'rs-1', rule_set_id: 'ru-1' })

    expect(result).toEqual(payload)
  })
})
