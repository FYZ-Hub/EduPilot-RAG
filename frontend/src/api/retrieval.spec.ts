import { afterEach, describe, expect, it, vi } from 'vitest'

import { API_BASE_URL, ApiError } from './client'
import { fetchRetrievalOptions, fetchSource } from './retrieval'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function stubFetch(response: Response | Error): ReturnType<typeof vi.fn> {
  const mock = vi.fn()
  if (response instanceof Error) {
    mock.mockRejectedValue(response)
  } else {
    mock.mockResolvedValue(response)
  }
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('retrieval options API', () => {
  it('reads /api/retrieval/options and keeps grade_years numeric', async () => {
    const mock = stubFetch(
      jsonResponse({
        majors: ['计算机科学与技术'],
        grade_years: [2025, 2026],
        semesters: ['2026-2027-1'],
        doc_categories: [
          { value: 'course_syllabus', label: '课程大纲' },
          { value: 'degree_plan', label: '培养方案' },
        ],
        active_dataset_version: '2026.1',
        demo_available: true,
      }),
    )

    const options = await fetchRetrievalOptions()

    expect(mock.mock.calls[0][0]).toBe(`${API_BASE_URL}/retrieval/options`)
    expect(options.majors).toEqual(['计算机科学与技术'])
    expect(options.grade_years).toEqual([2025, 2026])
    expect(options.grade_years.every((value) => typeof value === 'number')).toBe(true)
    expect(options.semesters).toEqual(['2026-2027-1'])
    expect(options.doc_categories).toEqual([
      { value: 'course_syllabus', label: '课程大纲' },
      { value: 'degree_plan', label: '培养方案' },
    ])
    expect(options.active_dataset_version).toBe('2026.1')
    expect(options.demo_available).toBe(true)
  })

  it('accepts an empty options payload without inventing values', async () => {
    stubFetch(
      jsonResponse({
        majors: [],
        grade_years: [],
        semesters: [],
        doc_categories: [],
        active_dataset_version: null,
        demo_available: false,
      }),
    )

    const options = await fetchRetrievalOptions()

    expect(options.majors).toEqual([])
    expect(options.grade_years).toEqual([])
    expect(options.doc_categories).toEqual([])
    expect(options.active_dataset_version).toBeNull()
  })
})

describe('source API', () => {
  it('maps every source field including locator information', async () => {
    const mock = stubFetch(
      jsonResponse({
        chunk_id: 'a'.repeat(64),
        doc_id: 'doc-1',
        file_name: '01-培养方案.pdf',
        file_type: 'pdf',
        document_version: '2026.1',
        effective_from: '2026-09-01',
        dataset_version: '2026.1',
        text: '毕业总学分：155.0',
        page_number: 4,
        sheet_name: null,
        row_start: null,
        row_end: null,
        section_title: '培养方案/总则',
      }),
    )

    const source = await fetchSource('a'.repeat(64))

    expect(mock.mock.calls[0][0]).toBe(`${API_BASE_URL}/sources/${'a'.repeat(64)}`)
    expect(source).toEqual({
      chunk_id: 'a'.repeat(64),
      doc_id: 'doc-1',
      file_name: '01-培养方案.pdf',
      file_type: 'pdf',
      document_version: '2026.1',
      effective_from: '2026-09-01',
      dataset_version: '2026.1',
      text: '毕业总学分：155.0',
      page_number: 4,
      sheet_name: null,
      row_start: null,
      row_end: null,
      section_title: '培养方案/总则',
    })
  })

  it('surfaces the safe not-found error with its code', async () => {
    stubFetch(
      jsonResponse(
        {
          code: 'SOURCE_NOT_FOUND',
          message: '来源不存在或不可检索',
          details: {},
          request_id: 'req-source-1',
        },
        404,
      ),
    )

    const error = await fetchSource('b'.repeat(64)).catch((caught) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('SOURCE_NOT_FOUND')
    expect(error.requestId).toBe('req-source-1')
  })
})
