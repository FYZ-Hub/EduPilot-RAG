import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '@/api/client'
import type { DemoJob, DemoStatus, SeedAccepted } from '@/api/demo'

const mocks = vi.hoisted(() => ({
  fetchDemoStatus: vi.fn(),
  seedDemo: vi.fn(),
  fetchDemoJob: vi.fn(),
}))

vi.mock('@/api/demo', async () => {
  const actual = await vi.importActual<typeof import('@/api/demo')>('@/api/demo')
  return {
    ...actual,
    fetchDemoStatus: mocks.fetchDemoStatus,
    seedDemo: mocks.seedDemo,
    fetchDemoJob: mocks.fetchDemoJob,
  }
})

import { useDemoStore } from './demo'

function status(overrides: Partial<DemoStatus> = {}): DemoStatus {
  return {
    enabled: true,
    state: 'empty',
    dataset_version: '2026.1',
    manifest_sha256: null,
    pipeline_fingerprint: 'fp',
    available_documents: 0,
    ready_documents: 0,
    failed_documents: 0,
    loaded: false,
    poll_after_seconds: 2,
    active_dataset_version: null,
    serving_previous_version: false,
    active_job_id: null,
    last_job_id: null,
    reason: null,
    ...overrides,
  }
}

function job(overrides: Partial<DemoJob> = {}): DemoJob {
  return {
    job_id: 'job-1',
    dataset_version: '2026.1',
    target_stage: 'completed',
    status: 'running',
    current_stage: 'parsing',
    total: 2,
    imported: 0,
    resumed: 0,
    skipped: 0,
    failed: 0,
    processed: 1,
    progress_percent: 50,
    poll_after_seconds: 2,
    created_at: null,
    started_at: null,
    finished_at: null,
    documents: [],
    errors: [],
    ...overrides,
  }
}

function accepted(overrides: Partial<SeedAccepted> = {}): SeedAccepted {
  return {
    job_id: 'job-1',
    dataset_version: '2026.1',
    target_stage: 'completed',
    status: 'queued',
    status_url: '/api/demo/jobs/job-1',
    poll_after_seconds: 2,
    reused_active_job: false,
    ...overrides,
  }
}

describe('demo store polling lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    mocks.fetchDemoStatus.mockReset()
    mocks.seedDemo.mockReset()
    mocks.fetchDemoJob.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps a single timer chain after seeding', async () => {
    mocks.seedDemo.mockResolvedValue(accepted())
    mocks.fetchDemoJob.mockResolvedValue(job({ status: 'running' }))

    const store = useDemoStore()
    await store.seed()

    expect(store.jobId).toBe('job-1')
    expect(store.polling).toBe(true)

    await vi.advanceTimersByTimeAsync(1)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(3)

    store.stopPolling()
  })

  it('uses poll_after_seconds and falls back to 2s when the field is missing', async () => {
    mocks.fetchDemoStatus.mockResolvedValue(status({ poll_after_seconds: null }))
    const store = useDemoStore()

    await store.loadStatus()

    expect(store.pollIntervalMs).toBe(2000)
  })

  it('stops polling at a terminal job and re-reads the dataset status', async () => {
    mocks.seedDemo.mockResolvedValue(accepted())
    mocks.fetchDemoJob.mockResolvedValue(job({ status: 'completed' }))
    mocks.fetchDemoStatus.mockResolvedValue(status({ state: 'empty', loaded: false }))

    const store = useDemoStore()
    await store.seed()
    await vi.advanceTimersByTimeAsync(1)

    expect(mocks.fetchDemoStatus).toHaveBeenCalled()
    expect(store.job?.status).toBe('completed')
    expect(store.polling).toBe(false)

    // job completed 绝不直接推导 loaded
    expect(store.loaded).toBe(false)

    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(1)
  })

  it('only reports loaded when the refreshed status says so', async () => {
    mocks.seedDemo.mockResolvedValue(accepted())
    mocks.fetchDemoJob.mockResolvedValue(job({ status: 'completed' }))
    mocks.fetchDemoStatus.mockResolvedValue(status({ state: 'loaded', loaded: true }))

    const store = useDemoStore()
    await store.seed()
    await vi.advanceTimersByTimeAsync(1)

    expect(store.loaded).toBe(true)
  })

  it('pauses after three consecutive network failures without marking the job as failed', async () => {
    mocks.seedDemo.mockResolvedValue(accepted())
    mocks.fetchDemoJob.mockRejectedValue(new TypeError('Failed to fetch'))

    const store = useDemoStore()
    await store.seed()

    await vi.advanceTimersByTimeAsync(1)
    expect(store.pollFailures).toBe(1)
    expect(store.reconnecting).toBe(true)

    await vi.advanceTimersByTimeAsync(2000)
    expect(store.pollFailures).toBe(2)

    await vi.advanceTimersByTimeAsync(4000)
    expect(store.pollFailures).toBe(3)
    expect(store.pollPaused).toBe(true)
    expect(store.polling).toBe(false)

    // 网络失败不得把服务端 job 改写成 failed
    expect(store.job).toBeNull()
    expect(store.lastPollError?.kind).toBe('network')

    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(3)
  })

  it('resumes polling manually after a pause', async () => {
    mocks.seedDemo.mockResolvedValue(accepted())
    mocks.fetchDemoJob.mockRejectedValue(new TypeError('Failed to fetch'))

    const store = useDemoStore()
    await store.seed()
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(4000)
    expect(store.pollPaused).toBe(true)

    mocks.fetchDemoJob.mockResolvedValue(job({ status: 'running' }))
    await store.reconnect()
    await vi.advanceTimersByTimeAsync(1)

    expect(store.pollPaused).toBe(false)
    expect(store.pollFailures).toBe(0)
    expect(store.job?.status).toBe('running')

    store.stopPolling()
  })

  it('recovers from a 404 job by re-reading the dataset status and resuming', async () => {
    mocks.seedDemo.mockResolvedValue(accepted({ job_id: 'job-old' }))
    mocks.fetchDemoJob.mockRejectedValue(
      new ApiError('任务不存在', { kind: 'http', status: 404, code: 'DEMO_JOB_NOT_FOUND' }),
    )
    mocks.fetchDemoStatus.mockResolvedValue(status({ active_job_id: 'job-new' }))

    const store = useDemoStore()
    await store.seed()
    await vi.advanceTimersByTimeAsync(1)

    expect(mocks.fetchDemoStatus).toHaveBeenCalled()
    expect(store.jobId).toBe('job-new')
    expect(store.pollPaused).toBe(false)

    store.stopPolling()
  })

  it('restores polling from active_job_id when re-entering the page', async () => {
    mocks.fetchDemoStatus.mockResolvedValue(status({ active_job_id: 'job-restore' }))
    mocks.fetchDemoJob.mockResolvedValue(job({ job_id: 'job-restore', status: 'running' }))

    const store = useDemoStore()
    await store.restore()

    expect(store.jobId).toBe('job-restore')
    expect(store.job?.status).toBe('running')

    store.stopPolling()
  })

  it('stops the timer when the page unloads', async () => {
    mocks.seedDemo.mockResolvedValue(accepted())
    mocks.fetchDemoJob.mockResolvedValue(job({ status: 'running' }))

    const store = useDemoStore()
    await store.seed()
    await vi.advanceTimersByTimeAsync(1)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(1)

    store.stopPolling()
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.fetchDemoJob).toHaveBeenCalledTimes(1)
  })
})
